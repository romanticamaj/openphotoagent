"""Mini benchmark (03 §6), pure: scores are rule-based over a golden set the user labelled.

* Classification: accuracy, per-class precision/recall/F1, macro-F1, confusion matrix, ECE, and the
  **coverage–accuracy curve** ("auto-accept X % of photos at Y % accuracy") over confidence thresholds τ.
* A photo the candidate did not answer (decode error, timeout) counts as wrong and is reported as ``failed``.
* The suggested τ is the one with the largest coverage whose accuracy still meets the target (03 §6.4); applying it
  changes a threshold only, so nothing is re-inferred (invariant 7).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

GOLDEN_MIN = 50  # 03 §6.1: 50–100 photos …
GOLDEN_PER_CLASS = 5  # … and at least 5 per class
TAUS = tuple(round(i * 0.05, 2) for i in range(20))  # 0.00 … 0.95
ECE_BINS = 10


def _f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


def classification(
    gold: Mapping[str, str], preds: Mapping[str, tuple[str, float]], classes: list[str]
) -> dict[str, Any]:
    n = len(gold)
    correct = sum(1 for a, g in gold.items() if a in preds and preds[a][0] == g)
    confusion: dict[str, dict[str, int]] = {c: {d: 0 for d in classes} for c in classes}
    for a, g in gold.items():
        if a in preds and g in confusion and preds[a][0] in confusion[g]:
            confusion[g][preds[a][0]] += 1
    per: dict[str, dict[str, float]] = {}
    for c in classes:
        tp = confusion[c][c]
        predicted = sum(confusion[g][c] for g in classes)
        actual = sum(1 for g in gold.values() if g == c)
        p = tp / predicted if predicted else 0.0
        r = tp / actual if actual else 0.0
        per[c] = {"precision": p, "recall": r, "f1": _f1(p, r)}
    curve = []
    for tau in TAUS:
        covered = [a for a in gold if a in preds and preds[a][1] >= tau]
        if tau == 0.0:
            covered = list(gold)
        ok = sum(1 for a in covered if a in preds and preds[a][0] == gold[a])
        curve.append(
            {"tau": tau, "coverage": len(covered) / n if n else 0.0, "accuracy": ok / len(covered) if covered else None}
        )
    return {
        "n": n,
        "accuracy": correct / n if n else 0.0,
        "failed": sum(1 for a in gold if a not in preds),
        "per_class": per,
        "macro_f1": sum(v["f1"] for v in per.values()) / len(per) if per else 0.0,
        "confusion": confusion,
        "coverage_curve": curve,
        "ece": _ece(gold, preds),
    }


def _ece(gold: Mapping[str, str], preds: Mapping[str, tuple[str, float]]) -> float:
    answered = [(preds[a][1], preds[a][0] == g) for a, g in gold.items() if a in preds]
    if not answered:
        return 0.0
    total = 0.0
    for b in range(ECE_BINS):
        lo, hi = b / ECE_BINS, (b + 1) / ECE_BINS
        inside = [(p, ok) for p, ok in answered if lo <= p < hi or (b == ECE_BINS - 1 and p == 1.0)]
        if inside:
            conf = sum(p for p, _ in inside) / len(inside)
            acc = sum(1 for _, ok in inside if ok) / len(inside)
            total += len(inside) / len(answered) * abs(acc - conf)
    return total


def latency(ms: list[float]) -> dict[str, float]:
    s = sorted(ms)
    if not s:
        return {"p50_ms": 0, "p95_ms": 0, "mean_ms": 0.0}

    def rank(q: float) -> float:
        return s[max(0, math.ceil(q * len(s)) - 1)]

    return {"p50_ms": rank(0.50), "p95_ms": rank(0.95), "mean_ms": sum(s) / len(s)}


def recommend(
    cands: list[dict[str, Any]], vram_mb: float, items: int | None = None, within_s: float | None = None
) -> str | None:
    """The best macro-F1 among candidates that fit the VRAM and, if given, finish ``items`` within ``within_s``."""
    fit = [
        c
        for c in cands
        if c["vram_mb"] <= vram_mb
        and (items is None or within_s is None or (c["items_per_s"] > 0 and items / c["items_per_s"] <= within_s))
    ]
    if not fit:
        return None
    return str(max(fit, key=lambda c: (c["macro_f1"], c["items_per_s"]))["name"])


def golden_ready(items: Mapping[str, str], classes: list[str]) -> tuple[bool, dict[str, Any] | None]:
    short = [c for c in classes if sum(1 for v in items.values() if v == c) < GOLDEN_PER_CLASS]
    if len(items) >= GOLDEN_MIN and not short:
        return True, None
    return False, {"n": len(items), "min": GOLDEN_MIN, "per_class": GOLDEN_PER_CLASS, "short": short}


def suggest_threshold(curve: list[dict[str, Any]], target: float = 0.95) -> dict[str, Any] | None:
    ok = [p for p in curve if p["accuracy"] is not None and p["accuracy"] >= target and p["coverage"] > 0]
    if not ok:
        return None
    best = max(ok, key=lambda p: (p["coverage"], -p["tau"]))
    return {"tau": best["tau"], "coverage": best["coverage"], "accuracy": best["accuracy"]}
