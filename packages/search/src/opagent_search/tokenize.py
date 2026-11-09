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

TOKENIZER_VERSION = "jieba-zh-hant@1+opencc-s2twp+bigram"
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


def _jieba() -> Any:
    import jieba

    jieba.setLogLevel(logging.WARNING)
    return jieba


@dataclass
class Tokenizer:
    custom_words: list[str] = field(default_factory=list)
    vocab_path: Path = VOCAB
    _jb: Any = None
    _index: dict[str, str] = field(default_factory=dict)
    _names: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        import jieba

        self._jb = jieba.Tokenizer()
        jieba.setLogLevel(logging.WARNING)
        _, t2s = _opencc()
        d = yaml.safe_load(self.vocab_path.read_text(encoding="utf-8")) if self.vocab_path.exists() else {"terms": []}
        for t in d.get("terms", []):
            self._names[t["id"]] = {"zh": t["zh"], "en": t["en"]}
            for s in [t["zh"], t["en"], *t.get("synonyms", [])]:
                self._index.setdefault(normalize(s), t["id"])
                self.custom_words.append(s)
        for w in self.custom_words:
            nw = normalize(w)
            if _CJK.fullmatch(nw):
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

    def query(self, text: str) -> ParsedQuery:
        words = self.words(text)
        keys = sorted({k for w in [*words, text] if (k := self.tag_key(w))})
        bi = self.bigrams(text)
        return ParsedQuery(
            words=words, bigram=bi or None, tag_keys=keys, english=" ".join(re.findall(r"[a-z]+", normalize(text)))
        )


def _lit(t: str) -> str:
    return "'" + t.replace("\\", "\\\\").replace("'", "''") + "'"


def to_tsquery(words: list[str], op: str = "&") -> str:
    """Quoted lexemes only: user input can never inject tsquery operators (SRCH-TOK-008)."""
    return f" {op} ".join(_lit(w) for w in words if w)
