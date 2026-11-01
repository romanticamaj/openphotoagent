"""Few-shot prototypes (03 §3.3.1)."""

from __future__ import annotations

from typing import Any


def alpha_for(n_examples: int) -> float:
    return 0.0


def class_scores(img: Any, txt: dict[str, Any], examples: dict[str, Any], n_examples: dict[str, int],
                 negatives: dict[str, Any] | None = None) -> dict[str, float]:
    return {}
