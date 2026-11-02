"""Score calibration: temperature scaling and Platt scaling (03 §3.3.3). Deterministic, numpy only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray


def ece(confidence: ArrayLike, correct: ArrayLike, bins: int = 15) -> float:
    """Expected calibration error with equal-width bins."""
    conf = np.asarray(confidence, dtype=float)
    ok = np.asarray(correct, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(conf, edges[1:-1], right=True), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(conf[m].mean() - ok[m].mean())
    return float(total)


def _nll(logits: NDArray[np.float64], labels: NDArray[np.int64], t: float) -> float:
    z = logits / t
    z = z - z.max(1, keepdims=True)
    logp = z - np.log(np.exp(z).sum(1, keepdims=True))
    return float(-logp[np.arange(len(labels)), labels].mean())


def fit_temperature(logits: ArrayLike, labels: ArrayLike, lo: float = 1e-3, hi: float = 10.0) -> float:
    """Minimise NLL over T by golden-section search in log-space (NLL is unimodal in T)."""
    lg = np.asarray(logits, dtype=float)
    lb = np.asarray(labels, dtype=np.int64)
    a, b = np.log(lo), np.log(hi)
    g = (np.sqrt(5) - 1) / 2
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = _nll(lg, lb, float(np.exp(c))), _nll(lg, lb, float(np.exp(d)))
    for _ in range(80):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = _nll(lg, lb, float(np.exp(c)))
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = _nll(lg, lb, float(np.exp(d)))
    return float(np.exp((a + b) / 2))


@dataclass(frozen=True)
class Platt:
    a: float
    b: float

    def __call__(self, raw: ArrayLike) -> NDArray[np.float64]:
        return 1.0 / (1.0 + np.exp(-(self.a * np.asarray(raw, dtype=float) + self.b)))

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "platt", "a": self.a, "b": self.b}


def fit_platt(raw: ArrayLike, y: ArrayLike, iters: int = 100) -> Platt:
    """1-D logistic regression by Newton's method with Platt's smoothed targets."""
    x = np.asarray(raw, dtype=float)
    yy = np.asarray(y, dtype=float)
    n_pos, n_neg = yy.sum(), len(yy) - yy.sum()
    t = np.where(yy > 0, (n_pos + 1) / (n_pos + 2), 1 / (n_neg + 2))
    w = np.zeros(2)
    X = np.stack([x, np.ones_like(x)], 1)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w)))
        grad = X.T @ (p - t)
        hess = X.T @ (X * (p * (1 - p))[:, None]) + 1e-9 * np.eye(2)
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-10:
            break
    return Platt(a=float(w[0]), b=float(w[1]))


def platt_from_dict(d: dict[str, Any]) -> Platt:
    if d.get("kind") != "platt":
        raise ValueError(f"not a platt calibration: {d.get('kind')!r}")
    return Platt(a=float(d["a"]), b=float(d["b"]))
