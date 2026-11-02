"""Face decisions (07 §2.2–§2.6). Pure: no I/O, no clock; thresholds come from the embedding space.

Precision first: an assignment is automatic only when the best person is clearly ahead, has enough neighbours,
the face is of good quality and the person is not flagged as changing with age or confusable with another;
everything else is a suggestion for a person to confirm, or stays in the unassigned pool.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .path_template import sanitize_segment

THRESHOLDS: dict[str, float] = {  # AuraFace starting values; per embedding space, calibrated later (07 §2.6)
    "auto": 0.60,
    "suggest": 0.45,
    "margin": 0.08,
    "margin_hard": 0.12,  # age_variation / confusable
    "merge": 0.55,
    "find_high": 0.55,
    "find_low": 0.42,
}
MIN_CORE = 2
MAX_FACES = 50
MIN_GROUP = 3
SKIP_CATEGORIES = {"screenshot"}
SKIP_MEDIA = {"document"}


# ---- quality (07 §2.2) -------------------------------------------------------
def quality_tier(*, min_side: float, yaw: float, laplacian: float, fiqa: float) -> int:
    """0 reject (bbox only, no embedding) · 1 low (searchable, never automatic) · 2 ok · 3 good."""
    if min_side < 32 or abs(yaw) > 75 or laplacian < 20:
        return 0
    if min_side < 48 or abs(yaw) > 45 or fiqa < 0.3:
        return 1
    if min_side >= 80 and abs(yaw) < 30 and fiqa >= 0.6:
        return 3
    return 2


def cap_faces(faces: list[dict[str, Any]], limit: int = MAX_FACES) -> tuple[list[dict[str, Any]], bool]:
    """At most ``limit`` faces per photo (the most certain), marking the photo ``truncated`` (FACE-DET-002)."""
    if len(faces) <= limit:
        return faces, False
    return sorted(faces, key=lambda f: -float(f["det_score"]))[:limit], True


def should_run(*, category: str | None = None, media_kind: str | None = None) -> bool:
    return category not in SKIP_CATEGORIES and media_kind not in SKIP_MEDIA


# ---- online assignment (07 §2.3) ---------------------------------------------
@dataclass(frozen=True)
class Decision:
    kind: str  # auto | suggested | unassigned
    person: str | None
    score: float
    margin: float


def person_score(cosines: Sequence[float]) -> float:
    top = sorted(cosines, reverse=True)[:3]
    return sum(top) / len(top) if top else 0.0


def margin_for(person: str, *, age_variation: Iterable[str] = (), t: dict[str, float] = THRESHOLDS) -> float:
    return t["margin_hard"] if person in set(age_variation) else t["margin"]


def decide_assignment(
    scores: dict[str, Sequence[float]],
    *,
    neighbours_of_p1: int,
    tier: int,
    rejected: Iterable[str] = (),
    age_variation: Iterable[str] = (),
    confusable: Iterable[frozenset[str]] = (),
    t: dict[str, float] = THRESHOLDS,
) -> Decision:
    """``scores``: candidate person → cosines of its nearest faces/prototypes. Rejected persons (cannot-link) are
    never candidates."""
    banned = set(rejected)
    ranked = sorted(
        ((p, person_score(c)) for p, c in scores.items() if p not in banned and c), key=lambda x: (-x[1], x[0])
    )
    if not ranked:
        return Decision("unassigned", None, 0.0, 0.0)
    p1, s1 = ranked[0]
    s2 = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = s1 - s2
    if s1 < t["suggest"]:
        return Decision("unassigned", None, s1, margin)
    aged = set(age_variation)
    pairs = set(confusable)
    close_twin = len(ranked) > 1 and frozenset({p1, ranked[1][0]}) in pairs and margin < t["margin_hard"]
    automatic = (
        s1 >= t["auto"]
        and margin >= margin_for(p1, age_variation=aged, t=t)
        and neighbours_of_p1 >= MIN_CORE
        and tier >= 2
        and p1 not in aged  # children / changing appearance: always a person's call (07 §2.6)
        and not close_twin
    )
    return Decision("auto" if automatic else "suggested", p1, s1, margin)


# ---- clusters and persons (07 §2.3–§2.4) --------------------------------------
def stable_ids(
    old: dict[str, set[str]], new: list[set[str]], new_id: Callable[[], str], min_jaccard: float = 0.5
) -> list[str]:
    """An id per new group: the old group's id when they share ≥ ``min_jaccard`` of their faces (each old id used
    once, best overlap first), else a fresh one — so unnamed groups keep their id across re-clustering."""
    pairs = []
    for i, g in enumerate(new):
        for oid, og in old.items():
            u = len(g | og)
            j = len(g & og) / u if u else 0.0
            if j >= min_jaccard:
                pairs.append((j, i, oid))
    out: list[str | None] = [None] * len(new)
    used: set[str] = set()
    for _j, i, oid in sorted(pairs, key=lambda x: (-x[0], x[1], x[2])):
        if out[i] is None and oid not in used:
            out[i] = oid
            used.add(oid)
    return [x if x is not None else new_id() for x in out]


def fold_small(counts: dict[str, int], minimum: int = MIN_GROUP) -> tuple[list[str], list[str]]:
    """Groups with fewer than ``minimum`` faces go to 「其他臉孔」 (FACE-CLU-004)."""
    shown = [k for k, n in counts.items() if n >= minimum]
    return shown, [k for k, n in counts.items() if n < minimum]


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


def merge_suggestions(
    protos: dict[str, list[Sequence[float]]], tau: float = THRESHOLDS["merge"]
) -> list[tuple[str, str, float]]:
    """Person pairs whose prototypes come within ``tau`` — listed for a person, never merged (FACE-MGT-003)."""
    ids = sorted(protos)
    out = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            best = max((_cos(x, y) for x in protos[a] for y in protos[b]), default=0.0)
            if best >= tau:
                out.append((a, b, round(best, 6)))
    return out


def folder_name(name: str) -> str:
    """A person's name as one safe path segment for ``{person}`` (FACE-MGT-005; CORE-TPL rules)."""
    seg = sanitize_segment(name)
    return seg if seg not in ("", ".", "..") else "_"


# ---- find this person (07 §2.5) ------------------------------------------------
def find_tiers(
    faces: dict[str, tuple[str, float]], t: dict[str, float] = THRESHOLDS
) -> dict[str, list[tuple[str, float]]]:
    """``faces``: face → (asset, best cosine to any reference). An asset scores its best face; 「確定」 ≥ find_high,
    「可能」 ≥ find_low."""
    best: dict[str, float] = {}
    for asset, s in faces.values():
        best[asset] = max(s, best.get(asset, -1.0))
    ranked = sorted(best.items(), key=lambda x: (-x[1], x[0]))
    return {
        "sure": [(a, s) for a, s in ranked if s >= t["find_high"]],
        "maybe": [(a, s) for a, s in ranked if t["find_low"] <= s < t["find_high"]],
    }


def reference_faces(assignments: Iterable[tuple[str, str]]) -> list[str]:
    """Only faces a person confirmed grow the reference set; automatic ones never do (no drift; FACE-FIND-003)."""
    return [f for f, kind in assignments if kind == "confirmed"]


def chinese_whispers(
    nodes: Sequence[str], edges: dict[tuple[str, str], float], seed: int = 0, iterations: int = 20,
    min_weight: float = THRESHOLDS["suggest"],
) -> list[set[str]]:
    """Graph clustering for pools too large for HDBSCAN (07 §2.3, > 50k faces): each node repeatedly takes the
    label with the highest summed edge weight among its neighbours. Edges below ``min_weight`` are ignored so weak
    bridges do not chain two people together. Deterministic for a given ``seed``."""
    import random

    nbrs: dict[str, dict[str, float]] = {n: {} for n in nodes}
    for (a, b), w in edges.items():
        if w >= min_weight and a in nbrs and b in nbrs:
            nbrs[a][b] = max(w, nbrs[a].get(b, 0.0))
            nbrs[b][a] = max(w, nbrs[b].get(a, 0.0))
    label = {n: n for n in nodes}
    order = sorted(nodes)
    rnd = random.Random(seed)
    for _ in range(iterations):
        rnd.shuffle(order)
        changed = False
        for n in order:
            if not nbrs[n]:
                continue
            votes: dict[str, float] = {}
            for m, w in nbrs[n].items():
                votes[label[m]] = votes.get(label[m], 0.0) + w
            best = max(sorted(votes), key=lambda k: votes[k])
            if best != label[n]:
                label[n], changed = best, True
        if not changed:
            break
    groups: dict[str, set[str]] = {}
    for n, lab in label.items():
        groups.setdefault(lab, set()).add(n)
    return sorted(groups.values(), key=lambda g: sorted(g)[0])
