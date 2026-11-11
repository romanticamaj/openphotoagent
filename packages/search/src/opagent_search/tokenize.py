"""Application-side tokenisation for PostgreSQL ``simple`` tsvectors (05 §1.3).

1. normalise: NFKC (full-width → half-width), lower case, zero-width characters removed, OpenCC s2twp (Simplified
   queries match Traditional captions);
2. Chinese: jieba with custom words (label aliases, place names, album names). jieba's dictionary is Simplified, so
   the text is segmented through a Simplified shadow and the boundaries are mapped back onto the Traditional text;
3. CJK bigram fallback: every CJK run also yields ``§xy`` bigrams (weight D) for recall when word AND finds < N;
4. weights: tag names/aliases + canonical keys = A, caption/OCR = B, places/collections = C, path stems + bigrams = D.

Pure functions apart from loading dictionaries once. The version string is recorded in
``search_profile.fts.tokenizer``; changing behaviour means bumping it and reindexing text.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from opagent_core.vocabularies import VOCAB_DIR

TOKENIZER_VERSION = "jieba-zh-hant@4+opencc-s2twp+bigram"  # @4: file-time dates indexed (SRCH-IDX-010); @3 SRCH-TOK-014
VOCAB = VOCAB_DIR / "general-photo.yaml"  # package data in opagent_core (ADR-0023)
_ZW = re.compile("[​‌‍⁠﻿­]")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]+")
_WORD = re.compile(r"[0-9a-z]+(?:['._-][0-9a-z]+)*|[぀-ヿ㐀-䶿一-鿿豈-﫿]+")
STOP = {"的", "了", "在", "是", "和", "與", "a", "an", "the", "of", "in", "on", "and", "with"}


@lru_cache(maxsize=1)
def _opencc() -> tuple[Any, Any]:
    import opencc

    return opencc.OpenCC("s2twp"), opencc.OpenCC("t2s")


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = _ZW.sub("", s).lower()
    s2t, _ = _opencc()
    return str(s2t.convert(s))


@dataclass
class ParsedQuery:
    words: list[str]
    bigram: list[str] | None
    tag_keys: list[str]
    english: str = ""
    alts: dict[str, list[str]] = field(default_factory=dict)  # word -> other names of its vocabulary term


def _jieba() -> Any:
    import jieba

    jieba.setLogLevel(logging.WARNING)
    return jieba


# words that frame a question about photos rather than say what is in them (ADR-0032)
FILLER = {
    "照片",
    "相片",
    "圖",
    "圖片",
    "張",
    "拍",
    "拍的",
    "幾張",
    "有幾張",
    "多少",
    "我",
    "有",
    "嗎",
    "呢",
    # place words on their own: 「兩隻狗在草地上」 made a photo show 上 (SRCH-TOK-017)
    "上",
    "下",
    "裡",
    "裡面",
    "上面",
    "下面",
    "旁",
    "旁邊",
    "附近",
    "中間",
    # quantity words: 「一張有很多彩色方塊的圖」 made a photo show 一張 and 很多 (SRCH-TOK-015)
    "一張",
    "一個",
    "一隻",
    "很多",
    "許多",
    "好多",
    "一些",
    "幾個",
    "好幾",
    # degree words: 「很開心」 made every photo show 很 (SRCH-TOK-013)
    "很",
    "非常",
    "十分",
    "相當",
    "超級",
    "有點",
    "真的",
    "photo",
    "photos",
    "picture",
    "pictures",
    "image",
    "images",
    "pic",
    "pics",
}


# numbers and measure words: 「好多隻貓」 said no photo showed 「隻」 (SRCH-TOK-016); 「三個小朋友」 is children
MEASURE = re.compile(r"^[一二兩三四五六七八九十百幾好多]*[隻個些位群條頭件本台輛把朵幅張顆塊片雙對]?$")


@dataclass(frozen=True)
class Concept:
    """One thing the photo must show: a vocabulary term with all its names, or a word the vocabulary does not know."""

    word: str
    key: str | None
    forms: tuple[str, ...]


@dataclass
class Tokenizer:
    custom_words: list[str] = field(default_factory=list)
    vocab_path: Path = VOCAB
    _jb: Any = None
    _index: dict[str, str] = field(default_factory=dict)
    _names: dict[str, dict[str, str]] = field(default_factory=dict)
    _forms: dict[str, list[str]] = field(default_factory=dict)
    _implies: dict[str, set[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        import jieba

        self._jb = jieba.Tokenizer()
        jieba.setLogLevel(logging.WARNING)
        _, t2s = _opencc()
        d = yaml.safe_load(self.vocab_path.read_text(encoding="utf-8")) if self.vocab_path.exists() else {"terms": []}
        for t in d.get("terms", []):
            self._names[t["id"]] = {"zh": t["zh"], "en": t["en"]}
            self._implies[t["id"]] = set(t.get("implies") or [])
            for s in [t["zh"], t["en"], *t.get("synonyms", [])]:
                self._index.setdefault(normalize(s), t["id"])
                self.custom_words.append(s)
                if " " not in s.strip():  # one lexeme: a phrase cannot be an OR alternative
                    self._forms.setdefault(t["id"], []).append(normalize(s))
        for w in self.custom_words:
            nw = normalize(w)
            # a single character is already a token; as a heavy custom word it would split real words apart
            if _CJK.fullmatch(nw) and len(nw) > 1:
                self._jb.add_word(t2s.convert(nw), freq=200000)

    # ---- words ----
    def _cut_cjk(self, run: str) -> list[str]:
        _, t2s = _opencc()
        simp = t2s.convert(run)
        if len(simp) != len(run):  # OpenCC changed the length: segment the Traditional text directly
            return [w for w in self._jb.cut(run, HMM=True) if w.strip()]
        out, i = [], 0
        for w in self._jb.cut(simp, HMM=True):
            out.append(run[i : i + len(w)])
            i += len(w)
        return [w for w in out if w.strip()]

    def words(self, text: str) -> list[str]:
        out: list[str] = []
        for m in _WORD.finditer(normalize(text)):
            tok = m.group(0)
            if _CJK.fullmatch(tok):
                out.extend(self._cut_cjk(tok))
            else:
                out.append(tok)
        return [w for w in out if w not in STOP]

    @staticmethod
    def bigrams(text: str) -> list[str]:
        out: list[str] = []
        for run in _CJK.findall(normalize(text)):
            out.extend("§" + run[i : i + 2] for i in range(len(run) - 1))
        return out

    def implied(self, key: str | None) -> set[str]:
        """The scenes an activity implies (ADR-0032 addendum): 衝浪 happens at the sea or a beach."""
        return set(self._implies.get(key or "", set()))

    def tag_key(self, word: str) -> str | None:
        return self._index.get(normalize(word))

    def term_name(self, key: str, lang: str) -> str | None:
        """What a person calls a vocabulary id; None when the tag is not a vocabulary id."""
        names = self._names.get(key)
        return names["zh" if lang.startswith("zh") else "en"] if names else None

    # ---- documents / queries ----
    def document(
        self,
        caption: str | None = None,
        tags: list[str] | None = None,
        category: str | None = None,
        places: list[str] | None = None,
        path: str | None = None,
    ) -> dict[str, list[str]]:
        a: list[str] = []
        for t in tags or []:
            a.extend(self.words(t))
            k = self.tag_key(t) or (t if "." in t else None)
            if k:
                a.append(k)
        if category:
            a.append(category)
        b = self.words(caption or "")
        c = [w for p in places or [] for w in self.words(p)]
        stem = re.split(r"[/\\]", path or "")
        d = [w for part in stem for w in self.words(re.sub(r"\.[a-z0-9]{2,5}$", "", part))]
        d += self.bigrams(" ".join([caption or "", *(tags or [])]))
        return {"A": a, "B": b, "C": c, "D": d}

    def vector_text(self, text: str) -> str | None:
        """What the image model is asked (ADR-0031). SigLIP does not separate Chinese queries from noise on real
        photos, English ones it does: CJK words go through the vocabulary to their English name; words it does not
        know are left out; when none is known the image route is skipped (None). Latin script is passed as it is."""
        if not _CJK.search(text):
            return text.strip() or None
        names = [n for k in self.query(text).tag_keys if (n := self.term_name(k, "en"))]
        latin = re.findall(r"[a-z][a-z0-9'-]*", normalize(text))
        out = list(dict.fromkeys([*names, *latin]))
        return " ".join(out) or None

    def concepts(self, text: str) -> list[Concept]:
        """The query's concepts (ADR-0032): filler and stop words out; words with the same vocabulary id are one."""
        out: list[Concept] = []
        seen: set[str] = set()
        for w in self.words(text):
            if normalize(w) in FILLER or MEASURE.fullmatch(normalize(w)):
                continue
            k = self.tag_key(w)
            ident = k or normalize(w)
            if ident in seen:
                continue
            seen.add(ident)
            forms = [normalize(w), *(f for f in self._forms.get(k, []) if f != normalize(w))] if k else [normalize(w)]
            out.append(Concept(word=w, key=k, forms=tuple(forms)))
        return out

    def query(self, text: str) -> ParsedQuery:
        words = self.words(text)
        keys = sorted({k for w in [*words, text] if (k := self.tag_key(w))})
        bi = self.bigrams(text)
        alts = {w: [f for f in self._forms.get(k, []) if f != normalize(w)] for w in words if (k := self.tag_key(w))}
        return ParsedQuery(
            words=words,
            bigram=bi or None,
            tag_keys=keys,
            english=" ".join(re.findall(r"[a-z]+", normalize(text))),
            alts={w: a for w, a in alts.items() if a},
        )


def to_tsquery_alts(words: list[str], alts: dict[str, list[str]], op: str = "&") -> str:
    """Like ``to_tsquery``, each word also matching the other names of its vocabulary term: 「海邊」 finds captions
    that say 沙灘 (SRCH-TOK-010)."""
    parts = []
    for w in words:
        if not w:
            continue
        forms = [w, *alts.get(w, [])]
        parts.append(f"({' | '.join(_lit(f) for f in forms)})" if len(forms) > 1 else _lit(w))
    return f" {op} ".join(parts)


def _lit(t: str) -> str:
    return "'" + t.replace("\\", "\\\\").replace("'", "''") + "'"


def to_tsquery(words: list[str], op: str = "&") -> str:
    """Quoted lexemes only: user input can never inject tsquery operators (SRCH-TOK-008)."""
    return f" {op} ".join(_lit(w) for w in words if w)
