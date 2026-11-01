"""Confidence calibration (03 §3.3.3)."""

from __future__ import annotations

from typing import Any


def ece(conf: Any, hit: Any, bins: int = 10) -> float:
    return 0.0


def fit_temperature(logits: Any, labels: Any) -> float:
    return 0.0


class Platt:
    def __call__(self, x: Any) -> Any:
        return x

    def to_dict(self) -> dict[str, Any]:
        return {}


def fit_platt(raw: Any, y: Any) -> Platt:
    return Platt()


def platt_from_dict(d: dict[str, Any]) -> Platt:
    return Platt()
