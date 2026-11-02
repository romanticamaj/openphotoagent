"""Stub."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Fused:
    asset_id: str
    score: float
    matched_by: list[str]


def route_weights(q: str) -> dict[str, float]:
    return {}


def fuse(routes: dict[str, list[str]], weights: dict[str, float], k: int = 60) -> list[Fused]:
    return []


def combine_vectors(t: list[float], i: list[float], alpha: float = 0.5) -> list[float]:
    return []


def representative(group: list[dict[str, Any]]) -> dict[str, Any]:
    return group[0]
