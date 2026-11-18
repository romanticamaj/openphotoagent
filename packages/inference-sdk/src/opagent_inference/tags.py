"""Tag normalisation pipeline (03 §3.2 PostProcessor): trim, width/case folding, synonyms → canonical,
blacklist, dedupe, new-term cap."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from opagent_core.vocabularies import VOCAB_DIR as SHARED_VOCAB_DIR

VOCAB_DIR = SHARED_VOCAB_DIR  # package data in opagent_core (ADR-0023)


@dataclass(frozen=True)
class Vocabulary:
    id: str
    terms: dict[str, dict[str, Any]]  # canonical id -> term
    index: dict[str, str]  # folded surface form -> canonical id
    blacklist: frozenset[str]


@dataclass(frozen=True)
class TagOut:
    label: str
    canonical_id: str | None
    score: float
    is_new: bool
    source: str = "vlm"


@dataclass
class TagResult:
    tags: list[TagOut] = field(default_factory=list)
    dropped_new: int = 0
    dropped_blacklist: int = 0


def fold(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip().casefold()


def load_vocabulary(name: str = "general-photo", root: Path = VOCAB_DIR) -> Vocabulary:
    d = yaml.safe_load((root / f"{name}.yaml").read_text(encoding="utf-8"))
    terms = {t["id"]: t for t in d["terms"]}
    index: dict[str, str] = {}
    for t in d["terms"]:
        for s in [t["zh"], t["en"], *t.get("synonyms", [])]:
            index.setdefault(fold(s), t["id"])
    return Vocabulary(d["id"], terms, index, frozenset(fold(b) for b in d.get("blacklist", [])))


def normalize_tags(
    tags: list[tuple[str, float]], vocab: Vocabulary, max_new: int = 5, max_tags: int = 20, lang: str = "zh"
) -> TagResult:
    out = TagResult()
    seen: set[str] = set()
    known: list[TagOut] = []
    new: list[TagOut] = []
    for label, score in tags:
        f = fold(label)
        if not f:
            continue
        if f in vocab.blacklist:
            out.dropped_blacklist += 1
            continue
        cid = vocab.index.get(f)
        key = cid or f
        if key in seen:
            continue
        seen.add(key)
        if cid:
            known.append(TagOut(str(vocab.terms[cid][lang]), cid, score, False))
        else:
            new.append(TagOut(unicodedata.normalize("NFKC", label).strip(), None, score, True))
    new_sorted = sorted(new, key=lambda t: -t.score)
    out.dropped_new = max(0, len(new_sorted) - max_new)
    keep_new = set(map(id, new_sorted[:max_new]))
    merged = known + [t for t in new if id(t) in keep_new]
    out.tags = merged[:max_tags]
    return out
