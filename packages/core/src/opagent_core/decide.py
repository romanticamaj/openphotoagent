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


def route_embedding(emb: Stage, n_examples: int, policy: Policy = Policy()) -> Route:
    return Route("vlm", None, 0.0, 0.0)


def decide(emb: Stage | None = None, vlm: VlmResult | None = None, rule_category: str | None = None,
           n_examples: int = 0, policy: Policy = Policy()) -> Outcome:
    return Outcome("other", None, 0.0, "embedding")
