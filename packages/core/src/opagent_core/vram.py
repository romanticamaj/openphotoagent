"""VRAM budget model (03 §4.1). Pure."""

from __future__ import annotations


def budget_gb(total: float, safety: float = 0.92, external: float = 0.0, desktop: float = 0.0) -> float:
    return 0.0


def can_coload(need_large: float, resident_small: float, budget: float) -> str:
    return ""
