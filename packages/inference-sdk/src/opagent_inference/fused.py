"""Fused structured output ``opagent.fused.v1``: schema, validation and repair (03 §3.0, §2.3; INF-JSON).

Model output is untrusted text (invariant 6): the parser only ever yields a well-formed :class:`FusedResult`
or raises :class:`OutputParseError` — never anything else.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import json_repair

MAX_CAPTION = 400
MAX_TAGS = 20
MAX_TAG_LEN = 40
KNOWN = ("caption", "tags", "category", "confidence", "contains_image_text")
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


class OutputParseError(ValueError):
    def __init__(self, raw: str = "", message: str = "model output is not valid fused JSON") -> None:
        super().__init__(message)
        self.raw = raw


@dataclass
class CategoryMap:
    keys: list[str]
    display: dict[str, dict[str, str]] = field(default_factory=dict)  # key -> {lang: label}
    aliases: dict[str, list[str]] = field(default_factory=dict)

    def resolve(self, value: str) -> str | None:
        v = value.strip()
        if v in self.keys:
            return v
        low = v.casefold()
        for k in self.keys:
            names = [k, *self.display.get(k, {}).values(), *self.aliases.get(k, [])]
            if any(low == n.casefold() for n in names):
                return k
        return None

    @property
    def fallback(self) -> str:
        return "other" if "other" in self.keys else self.keys[-1]


@dataclass
class FusedResult:
    caption: str
    tags: list[str]
    category: str
    confidence: str | None = None
    contains_image_text: bool = False
    repaired: bool = False
    retried: bool = False
    flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extra_dropped: list[str] = field(default_factory=list)
    caption_lang: str | None = None
    raw: str = ""


def fused_schema(cats: CategoryMap, with_confidence: bool = True) -> dict[str, Any]:
    """Key order caption → tags → category (03 §3.0 default: "describe, then classify")."""
    props: dict[str, Any] = {
        "caption": {"type": "string", "maxLength": MAX_CAPTION},
        "tags": {"type": "array", "items": {"type": "string", "maxLength": MAX_TAG_LEN}, "maxItems": MAX_TAGS},
        "category": {"type": "string", "enum": list(cats.keys)},
    }
    if with_confidence:
        props["confidence"] = {"type": "string", "enum": ["high", "medium", "low"]}
    props["contains_image_text"] = {"type": "boolean"}
    return {
        "$id": "opagent.fused.v1",
        "type": "object",
        "properties": props,
        "required": ["caption", "tags", "category", *(["confidence"] if with_confidence else [])],
        "additionalProperties": False,
    }


def _strict(raw: str) -> dict[str, Any] | None:
    try:
        v = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return v if isinstance(v, dict) else None


def _lenient(raw: str) -> dict[str, Any] | None:
    m = _FENCE.search(raw)
    body = m.group(1) if m else raw
    start = body.find("{")
    if start < 0:
        return None
    body = body[start:]
    end = body.rfind("}")
    candidate = body[: end + 1] if end > 0 else body
    v = _strict(candidate)
    if v is not None:
        return v
    try:
        rep: Any = json_repair.loads(body)
    except Exception:
        return None
    return rep if isinstance(rep, dict) and rep else None


def _clip(s: str, n: int) -> str:
    return s[:n]


def _coerce(obj: dict[str, Any], cats: CategoryMap, raw: str) -> FusedResult:
    caption = obj.get("caption")
    tags = obj.get("tags")
    category = obj.get("category")
    if not isinstance(caption, str) or not isinstance(category, str) or not isinstance(tags, list):
        raise OutputParseError(raw, "missing or mistyped required fields")
    res = FusedResult(caption=_clip(caption, MAX_CAPTION), tags=[], category=cats.fallback, raw=raw)
    seen: set[str] = set()
    for t in tags:
        if not isinstance(t, str) or not t.strip():
            continue
        t = _clip(t.strip(), MAX_TAG_LEN)
        if t not in seen:
            seen.add(t)
            res.tags.append(t)
    if len(res.tags) > MAX_TAGS:
        res.tags = res.tags[:MAX_TAGS]
    mapped = cats.resolve(category)
    if mapped is None:
        res.flags.append("enum_violation")
    else:
        res.category = mapped
    conf = obj.get("confidence")
    res.confidence = conf if conf in ("high", "medium", "low") else None
    res.contains_image_text = obj.get("contains_image_text") is True
    extra = [k for k in obj if k not in KNOWN]
    if extra:
        res.extra_dropped = extra
        res.warnings.append(f"dropped additional properties: {', '.join(map(str, extra))}")
    return res


def parse_fused(
    raw: str,
    cats: CategoryMap,
    retry: Callable[[], str] | None = None,
    caption_lang: str | None = None,
) -> FusedResult:
    """Strict JSON → lenient (fences, chatter, json-repair) → one temperature-0 "JSON only" retry → error."""
    obj = _strict(raw)
    repaired = False
    if obj is None:
        obj = _lenient(raw)
        repaired = obj is not None
    if obj is not None:
        try:
            res = _coerce(obj, cats, raw)
            res.repaired, res.caption_lang = repaired, caption_lang
            return res
        except OutputParseError:
            if retry is None:
                raise
    if retry is not None:
        again = retry()
        try:
            res = parse_fused(again, cats, retry=None, caption_lang=caption_lang)
        except OutputParseError:
            err = OutputParseError(raw, "model output is not valid fused JSON after one retry")
            err.retry_raw = again  # type: ignore[attr-defined]
            raise err from None
        res.retried = True
        return res
    raise OutputParseError(raw)


_CATEGORY_VALUE = re.compile(r'(?<!\\)"category"\s*:\s*"$')
MIN_PROB = 1e-6  # a candidate absent from top_logprobs (CORE-THR-013)


def category_probability(tokens: list[dict[str, Any]] | None, chosen: str, candidates: list[str]) -> float | None:
    """P(chosen) at the first token of the ``category`` value, normalised over the offered classes (03 §3.3.3).
    None when the runtime gave no logprobs, or the field cannot be found: the caller falls back to the self-report."""
    acc = ""
    for t in tokens or []:
        if _CATEGORY_VALUE.search(acc):
            tops = {str(x["token"]).strip(): float(x["logprob"]) for x in t.get("top_logprobs") or []}
            raw = {
                c: max((math.exp(v) for k, v in tops.items() if len(k) >= 2 and c.startswith(k)), default=MIN_PROB)
                for c in candidates
            }
            total = sum(raw.values())
            return raw[chosen] / total if tops and chosen in raw else None
        acc += str(t.get("token", ""))
    return None
