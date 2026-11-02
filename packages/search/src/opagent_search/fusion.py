"""Pure ranking pieces of hybrid search (05 §2.1–2.2).

* Weighted reciprocal-rank fusion: ``score(d) = Σ_r w_r / (k + rank_r(d))``, k = 60; R1 image vector 1.0,
  R3 full text 1.0 (1.5 when the query has a quoted phrase), R4 tag 1.2. Ties break by asset id (stable pages).
* Text + image query vector: ``normalize(α·t + (1−α)·i)``.
* Duplicate-group representative: highest resolution → largest file → earliest ``taken_at``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

RRF_K = 60
BASE_WEIGHTS = {"image_vector": 1.0, "fulltext": 1.0, "tag": 1.2, "caption_vector": 0.8}  # R2: 05 §2
PHRASE_FULLTEXT = 1.5


@dataclass
class Fused:
    asset_id: str
    score: float
    matched_by: list[str]


def route_weights(q: str) -> dict[str, float]:
    w = dict(BASE_WEIGHTS)
    if re.search(r'"[^"]+"|「[^」]+」', q or ""):
        w["fulltext"] = PHRASE_FULLTEXT
    return w


def fuse(routes: dict[str, list[str]], weights: dict[str, float], k: int = RRF_K) -> list[Fused]:
    scores: dict[str, float] = {}
    matched: dict[str, list[str]] = {}
    for name in sorted(routes):
        for rank, aid in enumerate(routes[name], 1):
            scores[aid] = scores.get(aid, 0.0) + weights.get(name, 1.0) / (k + rank)
            matched.setdefault(aid, []).append(name)
    return [Fused(a, scores[a], matched[a]) for a in sorted(scores, key=lambda a: (-scores[a], a))]


def combine_vectors(t: list[float], i: list[float], alpha: float = 0.5) -> list[float]:
    v = [alpha * a + (1 - alpha) * b for a, b in zip(t, i, strict=True)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else [0.0] * len(v)


def representative(group: list[dict[str, Any]]) -> dict[str, Any]:
    def key(a: dict[str, Any]) -> tuple[int, int, float, str]:
        px = int(a.get("width") or 0) * int(a.get("height") or 0)
        t: datetime | None = a.get("taken_at")
        when = t.timestamp() if t is not None else math.inf
        return (-px, -int(a.get("size_bytes") or 0), when, str(a.get("id")))

    return min(group, key=key)
