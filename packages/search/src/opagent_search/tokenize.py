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

# @17 煙火, 泡泡, 烤肉 and others; @16 露營, 帳篷; @15 帽子; @14 籃球架, 書桌 (ADR-0048); history in git
TOKENIZER_VERSION = "jieba-zh-hant@17+opencc-s2twp+bigram"
# Names that hold a vocabulary word but are not that thing: a caption reading a sign 「日落大道」 (Sunset Blvd) is not a
# sunset (SRCH-TOK-018). Weighed above the vocabulary's words, so the name is one word.
NAMES = ("日落大道", "夕陽大道")
VOCAB = VOCAB_DIR / "general-photo.yaml"  # package data in opagent_core (ADR-0023)
_ZW = re.compile("[​‌‍⁠﻿­]")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]+")
_WORD = re.compile(r"[0-9a-z]+(?:['._-][0-9a-z]+)*|[぀-ヿ㐀-䶿一-鿿豈-﫿]+")
# verbs that only say what one does with the thing named next (SRCH-TOK-024): 去爬山, 搭飛機, 堆沙堡, 看海
LIGHT_VERBS = {"去", "搭", "坐", "堆", "跳", "騎", "看", "玩", "放", "做"}
STOP = {"的", "了", "在", "是", "和", "與", "a", "an", "the", "of", "in", "on", "and", "with"}


@lru_cache(maxsize=1)
def _opencc() -> tuple[Any, Any]:
    import opencc

    return opencc.OpenCC("s2twp"), opencc.OpenCC("t2s")


def to_hant(text: str | None) -> str | None:
    """Text as zh-TW keeps it (Q-030): a caption the model wrote with Simplified characters, 「开心」, becomes 「開心」.
    Only characters Traditional text cannot have are converted, those outside Big5 (cp950): converting whole text
    also rewrote correct Taiwan wording, 設備→裝置, 台→臺, 家具→傢俱 (SRCH-TOK-019). Latin text passes through."""
    if not text:
        return text
    out = []
    for ch in text:
        if "一" <= ch <= "鿿" and not _big5(ch):
            ch = str(_s2tw().convert(ch))
        out.append(ch)
    return "".join(out)


def _big5(ch: str) -> bool:
    try:
        ch.encode("cp950")
    except UnicodeEncodeError:
        return False
    return True


@lru_cache(maxsize=1)
def _s2tw() -> Any:
    import opencc

    return opencc.OpenCC("s2tw")


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
    _includes: dict[str, set[str]] = field(default_factory=dict)
    _beside: dict[str, set[str]] = field(default_factory=dict)
    _no_theme: set[str] = field(default_factory=set)
    _suggest: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        import jieba

        self._jb = jieba.Tokenizer()
        jieba.setLogLevel(logging.WARNING)
        _, t2s = _opencc()
        d = yaml.safe_load(self.vocab_path.read_text(encoding="utf-8")) if self.vocab_path.exists() else {"terms": []}
        for t in d.get("terms", []):
            self._names[t["id"]] = {"zh": t["zh"], "en": t["en"]}
            self._implies[t["id"]] = set(t.get("implies") or [])
            self._includes[t["id"]] = set(t.get("includes") or [])
            self._beside[t["id"]] = set(t.get("ranks_beside") or [])
            self._suggest[t["id"]] = list(t.get("suggest") or [])  # what an event is seen by (SRCH-TOK-027)
            if t.get("theme") is False:  # true of any year: not a memory theme (MEM-012)
                self._no_theme.add(t["id"])
            # words a caption uses for it that already name another term: 球 is in 足球, 踢球 (SRCH-TOK-025); matched in
            # text only, never a name of this term, never a new word to the segmenter
            for f in t.get("text_forms") or []:
                self._forms.setdefault(t["id"], []).append(normalize(f))
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
        for name in NAMES:
            self._jb.add_word(t2s.convert(normalize(name)), freq=400000)

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

    def included(self, key: str | None) -> set[str]:
        """The kinds a broader term includes (SRCH-TOK-020): 車子 is a car, a bus, a train or a bicycle."""
        return set(self._includes.get(key or "", set()))

    def _forms_of(self, key: str) -> list[str]:
        """A term's own names, then those of the kinds it includes: a photo of any of them shows it."""
        out = list(self._forms.get(key, []))
        for k in sorted(self.included(key)):
            out += [f for f in self._forms.get(k, []) if f not in out]
        return out

    def implied(self, key: str | None) -> set[str]:
        """The scenes an activity implies (ADR-0032 addendum): 衝浪 happens at the sea or a beach."""
        return set(self._implies.get(key or "", set()))

    def bigram_query(self, text: str) -> str:
        """The character-pair fallback (05 §1.3 rule 3) as a tsquery: each word's pairs AND-ed; a vocabulary word by
        any of its Chinese names, OR-ed, so 沙灘 and 海邊 find the same photos (SRCH-TOK-025)."""
        parts: list[str] = []
        for w in self.words(text):
            if not _CJK.search(w):
                continue
            k = self.tag_key(w)
            names = [f for f in self._forms_of(k) if _CJK.fullmatch(f) and len(f) > 1] if k else []
            names = list(dict.fromkeys([normalize(w), *names])) if names else [normalize(w)]
            groups = [to_tsquery(self.bigrams(n)) for n in names if self.bigrams(n)]
            if groups:
                parts.append(groups[0] if len(groups) == 1 else "(" + " | ".join(f"({g})" for g in groups) + ")")
        return " & ".join(parts)

    def without_verbs(self, text: str) -> str:
        """A light verb before other words is not something a photo shows: 「去爬山」 is 「爬山」, 「搭飛機」 「飛機」
        (SRCH-TOK-024); then 吃 beside a food (SRCH-TOK-023)."""
        out = text
        for c in self.concepts(text):  # one word to the segmenter, a verb and a term to a person: 看海
            w = normalize(c.word)
            if c.key is None and len(w) > 1 and w[0] in LIGHT_VERBS and self.tag_key(w[1:]):
                out = out.replace(c.word, " " + c.word[1:], 1)
        cs = self.concepts(out)
        verbs = [c for c in cs if c.key is None and normalize(c.word) in LIGHT_VERBS]
        if verbs and len(verbs) < len(cs):
            for v in verbs:
                out = out.replace(v.word, " ", 1)
        out = re.sub(r"\s+", " ", out).strip()
        return self.without_eating(out)

    def without_eating(self, text: str) -> str:
        """吃 beside only foods, or words the vocabulary does not know, is eating that thing: 「吃蛋糕」 is 「蛋糕」 and
        「吃香蕉」 「香蕉」 (SRCH-TOK-023). Beside anything else (a place), or alone, it stays a concept."""
        cs = self.concepts(text)
        verbs = [c for c in cs if self.ranks_beside(c.key)]
        others = [c for c in cs if c not in verbs]
        if not verbs or not others:
            return text
        for c in others:
            if c.key is None and c.word.isascii():
                return text
            if c.key is not None and not any(c.key.split(".")[0] == g for v in verbs for g in self.ranks_beside(v.key)):
                return text
        out = text
        for v in verbs:
            out = (
                re.sub(rf"(?<![a-z]){re.escape(v.word)}(?![a-z])", " ", out, count=1)
                if v.word.isascii()
                else out.replace(v.word, " ", 1)
            )
        return re.sub(r"\s+", " ", out).strip()

    def not_themes(self) -> frozenset[str]:
        """Terms the vocabulary marks ``theme: false``: true of any year (室內, 玩耍), never a memory theme."""
        return frozenset(self._no_theme)

    def ranks_beside(self, key: str | None) -> set[str]:
        """The kinds of term beside which this one only ranks (SRCH-CNC-020): 吃 beside a food is eating that food."""
        return set(self._beside.get(key or "", set()))

    def tag_key(self, word: str) -> str | None:
        return self._index.get(normalize(word))

    def related(self, text: str) -> list[str]:
        """The vocabulary's words written inside a query, by their Chinese name, longest match first; not the query's
        own term (SRCH-TOK-022): what 「爬山」 can try when it finds nothing."""
        q = normalize(text)
        own = self.tag_key(q)
        found: dict[str, int] = {}
        for form, key in self._index.items():
            if key != own and _CJK.fullmatch(form) and form in q and form != q:
                found[key] = max(found.get(key, 0), len(form))
        keys = sorted(found, key=lambda k: (-found[k], k))
        # an event names the things it is seen by: 生日 tries 蛋糕 (SRCH-TOK-027)
        for k in [own or "", *list(found)]:  # 「生日派對」 holds 生日: its suggestions too (SRCH-TOK-029)
            keys += [x for x in self._suggest.get(k, []) if x not in keys]
        return [n for k in keys if (n := self.term_name(k, "zh"))]

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
        know are left out; when none is known the image route is skipped (None). Latin script is passed as it is, but
        a vocabulary word in it asks by its concept's English name, as the Chinese word does (SRCH-TOK-021)."""
        if not _CJK.search(text):
            if not text.strip():
                return None
            if (k := self.tag_key(text)) and (n := self.term_name(k, "en")):
                return n
            parts = re.split(r"([a-z][a-z0-9'-]*)", normalize(text))
            for i in range(1, len(parts), 2):
                if (k := self.tag_key(parts[i])) and (n := self.term_name(k, "en")):
                    parts[i] = n
            return "".join(parts).strip() or None
        # the query's own terms: 車子 asks for "vehicle", not for each kind it includes (SRCH-TOK-020)
        own = sorted({k for w in [*self.words(text), text] if (k := self.tag_key(w))})
        names = [n for k in own if (n := self.term_name(k, "en"))]
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
            forms = [normalize(w), *(f for f in self._forms_of(k) if f != normalize(w))] if k else [normalize(w)]
            out.append(Concept(word=w, key=k, forms=tuple(forms)))
        return out

    def query(self, text: str) -> ParsedQuery:
        words = self.words(text)
        own = {k for w in [*words, text] if (k := self.tag_key(w))}
        keys = sorted(own | {i for k in own for i in self.included(k)})  # its kinds' tags too (SRCH-TOK-020)
        bi = self.bigrams(text)
        alts = {w: [f for f in self._forms_of(k) if f != normalize(w)] for w in words if (k := self.tag_key(w))}
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
