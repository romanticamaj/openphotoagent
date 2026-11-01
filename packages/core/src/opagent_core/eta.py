"""ETA estimation (docs/06 §5.5). Pure: callers pass timestamps from a Clock; nothing here reads time."""

from __future__ import annotations

import math
from typing import Any

N0 = {"history": 30, "registry_prior": 10, "time_model": 5}  # prior strength (06 §5.5.2)
Z80 = 1.28  # p10/p90 for a normal approximation
SIGMA_CAP = 0.9
PRECISE_MAX_WIDTH = 0.2
ROUGH_MIN_WIDTH = 0.4
PRECISE_MAX_P50 = 600.0
HYSTERESIS_S = 10.0
STALL_MIN_S = 30.0


class Ewma:
    """Time-weighted EWMA with exponentially weighted variance (06 §5.5.2)."""

    def __init__(self, tau: float) -> None:
        self.tau = tau
        self.mean: float | None = None
        self.var = 0.0

    def add(self, x: float, dt: float) -> float:
        if self.mean is None:
            self.mean = x
            return x
        a = 1 - math.exp(-dt / self.tau)
        d = x - self.mean
        self.mean += a * d
        self.var = (1 - a) * (self.var + a * d * d)
        return self.mean


class BytesRate:
    """Two time constants (fast 5 s, slow 30 s); paused time is never sampled (ETA-001/002)."""

    def __init__(self, tau_fast: float = 5.0, tau_slow: float = 30.0) -> None:
        self._f, self._s = Ewma(tau_fast), Ewma(tau_slow)
        self.last_t, self.last_total = 0.0, 0
        self.paused = False

    @property
    def fast(self) -> float:
        return self._f.mean or 0.0

    @fast.setter
    def fast(self, v: float) -> None:
        self._f.mean = v

    @property
    def slow(self) -> float:
        return self._s.mean or 0.0

    @slow.setter
    def slow(self, v: float) -> None:
        self._s.mean = v

    def update(self, t: float, total: int) -> None:
        dt = t - self.last_t
        if self.paused or dt <= 0:
            return
        x = (total - self.last_total) / dt
        self._f.add(x, dt)
        self._s.add(x, dt)
        self.last_t, self.last_total = t, total

    def pause(self, t: float) -> None:
        self.paused = True

    def resume(self, t: float) -> None:
        self.paused = False
        self.last_t = t

    def speed(self, elapsed: float) -> float:
        return self.fast if elapsed < 10.0 else 0.3 * self.fast + 0.7 * self.slow


def shrink(prior: float, basis: str, observed: float | None, n: int) -> float:
    """Bayesian shrinkage of a prior rate towards the observed rate (ETA-004)."""
    if observed is None or n <= 0:
        return prior
    n0 = N0.get(basis, 5)
    return (n0 * prior + n * observed) / (n0 + n)


def select_prior(same_sig: float | None, other_sig: tuple[float, int, int] | None, registry: float | None,
                 gpu_factor: float = 1.0) -> tuple[float | None, str]:
    """Prior source order (06 §5.5.3): same task_sig history, other task_sig scaled by output tokens,
    registry perf_prior x GPU factor, none (ETA-005). ``other_sig = (rate, its_tokens, this_tokens)``."""
    if same_sig is not None:
        return same_sig, "history"
    if other_sig is not None:
        rate, other_tokens, this_tokens = other_sig
        return rate * other_tokens / this_tokens, "history"
    if registry is not None:
        return registry * gpu_factor, "registry_prior"
    return None, "none"


def multi_phase(phases: list[tuple[float, float]]) -> tuple[float, float]:
    """Sum of phase p50s; variances add (phases independent, 06 §5.5.4)."""
    return sum(p for p, _ in phases), sum(v for _, v in phases)


def interval(p50: float, sigma_rel: float) -> tuple[float, float]:
    s = min(sigma_rel, SIGMA_CAP)
    return p50 * (1 - Z80 * s), p50 * (1 + Z80 * s)


class EtaTracker:
    """eta.state machine (06 §5.5.6): estimating / rough / precise / unknown / stalled, with hysteresis."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.current = "estimating"
        self._eligible_since: float | None = None
        self._last_progress_t: float | None = None
        self._batch_interval = 0.0

    def state(self, elapsed: float, samples: int, has_prior: bool, phase: str | None = None) -> str:
        if phase == "enumerate" and not has_prior:
            return "unknown"
        if elapsed < 3.0:
            return "estimating"
        if self.kind == "bytes" and samples < 4:
            return "estimating"
        if self.kind == "items" and not has_prior and samples < 2:
            return "estimating"
        return "rough"

    def update(self, t: float, p50: float, p10: float, p90: float, samples: int, has_prior: bool,
               observed: bool) -> str:
        width = (p90 - p10) / p50 if p50 > 0 else math.inf
        eligible = observed and width <= PRECISE_MAX_WIDTH and p50 < PRECISE_MAX_P50
        if not eligible:
            self._eligible_since = None
            self.current = "rough"  # downgrade is immediate
        else:
            if self._eligible_since is None:
                self._eligible_since = t
            if t - self._eligible_since >= HYSTERESIS_S:
                self.current = "precise"
            elif self.current != "precise":
                self.current = "rough"
        return self.current

    def progress(self, t: float, done: int, batch_interval: float) -> None:
        self._last_progress_t = t
        self._batch_interval = batch_interval

    def stalled_state(self, t: float, reason: str | None = None) -> dict[str, Any] | None:
        if self._last_progress_t is None:
            return None
        limit = max(STALL_MIN_S, 5 * self._batch_interval)
        if t - self._last_progress_t <= limit:
            return None
        return {"state": "stalled", "since_s": t - self._last_progress_t, "reason": reason,
                "ctas": [{"id": "view_logs", "label_key": "eta.cta.view_logs"},
                         {"id": "retry", "label_key": "eta.cta.retry"}]}
