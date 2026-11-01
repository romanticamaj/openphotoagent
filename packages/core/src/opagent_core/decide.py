"""Threshold decision and confidence (docs/03 §3.3, §3.3.3; 09a §1.3). Pure function."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Decision = Literal["accepted", "other", "needs_review"]
Source = Literal["rule", "embedding", "vlm", "consensus"]


@dataclass(frozen=True)
class Policy:
    tau_high: float = 0.80
    margin: float = 0.15
    tau_high_no_examples: float = 0.90
    min_confidence: float = 0.55
    accept: float = 0.80
    review: float = 0.50


@dataclass(frozen=True)
class Stage:
    scores: dict[str, float]


@dataclass(frozen=True)
class VlmResult:
    category: str
    p: float


@dataclass(frozen=True)
class Route:
    route: Literal["accept", "vlm"]
    category: str | None
    p: float
    margin: float
    hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class Outcome:
    decision: Decision
    category: str | None
    confidence: float
    source: Source
    reason: str | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)


EPS = 1e-9  # threshold comparisons are inclusive (CORE-THR-003) and robust to float rounding


def _ranked(emb: Stage) -> list[tuple[str, float]]:
    return sorted(emb.scores.items(), key=lambda kv: (-kv[1], kv[0]))


def route_embedding(emb: Stage, n_examples: int, policy: Policy = Policy()) -> Route:
    """Stage E: accept directly when confident and well separated; otherwise send to the VLM with top-3 hints."""
    ranked = _ranked(emb)
    if not ranked:
        return Route("vlm", None, 0.0, 0.0)
    cat, p1 = ranked[0]
    p2 = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = p1 - p2
    tau = policy.tau_high if n_examples > 0 else policy.tau_high_no_examples
    hints = tuple(c for c, _ in ranked[:3])
    if p1 + EPS >= tau and margin + EPS >= policy.margin:
        return Route("accept", cat, p1, margin, hints)
    return Route("vlm", cat, p1, margin, hints)


def _by_thresholds(cat: str | None, p: float, source: Source, policy: Policy, reason: str = "low_confidence") -> Outcome:
    if cat is None or p + EPS < policy.min_confidence:
        return Outcome("other", None, p, source)
    if p + EPS >= policy.accept:
        return Outcome("accepted", cat, p, source)
    if p + EPS >= policy.review:
        return Outcome("needs_review", cat, p, source, reason)
    return Outcome("other", None, p, source)


def decide(emb: Stage | None = None, vlm: VlmResult | None = None, rule_category: str | None = None,
           n_examples: int = 0, policy: Policy = Policy()) -> Outcome:
    """Final decision for one photo.

    * rule hit wins (confidence 1.0);
    * VLM present: ``other`` is final; with an embedding stage, agreement -> consensus ``1-(1-pE)(1-pV)``,
      disagreement -> VLM category at ``0.8·pV``; both then judged by task thresholds (accept/review);
      VLM alone is accepted at ``min_confidence`` (it is the final judge of the cascade);
    * embedding only: route accept -> accepted, else task thresholds.
    """
    if rule_category is not None:
        return Outcome("accepted", rule_category, 1.0, "rule")
    if vlm is not None:
        if vlm.category == "other":
            return Outcome("other", None, vlm.p, "vlm")
        if emb is not None and emb.scores:
            top_cat, p_e = _ranked(emb)[0]
            if top_cat == vlm.category:
                p = 1 - (1 - p_e) * (1 - vlm.p)
                return _by_thresholds(vlm.category, p, "consensus", policy)
            return _by_thresholds(vlm.category, vlm.p * 0.8, "vlm", policy, reason="conflict")
        if vlm.p + EPS >= policy.min_confidence:
            return Outcome("accepted", vlm.category, vlm.p, "vlm")
        return Outcome("other", None, vlm.p, "vlm")
    if emb is None or not emb.scores:
        return Outcome("other", None, 0.0, "embedding")
    r = route_embedding(emb, n_examples, policy)
    if r.route == "accept":
        return Outcome("accepted", r.category, r.p, "embedding")
    return _by_thresholds(r.category, r.p, "embedding", policy)


@dataclass(frozen=True)
class TaxonomyEntry:
    id: str
    key: str
    display: str


def temperature_softmax(sims: list[float], t: float) -> list[float]:
    return []


def normalize_logprobs(first_token_logprobs: dict[str, float], candidates: list[str]) -> dict[str, float]:
    return {}


def build_taxonomy(classes: list[tuple[str, str]], first_token: object) -> list[TaxonomyEntry]:
    return []


def redecide(stages: dict[str, object], policy: Policy) -> Outcome:
    return Outcome("other", None, 0.0, "embedding")
