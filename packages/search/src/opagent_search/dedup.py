"""Stub."""

from __future__ import annotations

from typing import Any


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def cosine(a: list[float], b: list[float]) -> float:
    import math

    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


def group_near(items: list[tuple[str, str, Any]], max_hamming: int = 6, min_cosine: float = 0.97) -> list[list[str]]:
    return []
