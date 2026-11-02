"""VRAM budget model (03 §4.1). Pure."""

from __future__ import annotations

from typing import Literal

Coload = Literal["ok", "sequential", "blocked"]


def budget_gb(total: float, safety: float = 0.92, external: float = 0.0, desktop: float = 0.0) -> float:
    """Usable VRAM = total × safety − other processes (games, browsers) − desktop compositor."""
    return total * safety - external - desktop


def can_coload(need_large: float, resident_small: float, budget: float) -> Coload:
    """ok: the large model fits next to the resident small one; sequential: only after unloading the small
    slot; blocked: the large model alone does not fit (needs a smaller tier or offload)."""
    if need_large + resident_small <= budget:
        return "ok"
    if need_large <= budget:
        return "sequential"
    return "blocked"
