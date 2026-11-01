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


def select_prior(
    same_sig: float | None, other_sig: tuple[float, int, int] | None, registry: float | None, gpu_factor: float = 1.0
) -> tuple[float | None, str]:
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

    def update(
        self, t: float, p50: float, p10: float, p90: float, samples: int, has_prior: bool, observed: bool
    ) -> str:
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
        return {
            "state": "stalled",
            "since_s": t - self._last_progress_t,
            "reason": reason,
            "ctas": [
                {"id": "view_logs", "label_key": "eta.cta.view_logs"},
                {"id": "retry", "label_key": "eta.cta.retry"},
            ],
        }


# ---- display (06 §5.5.6) ----
def _round_minutes(seconds: float) -> int:
    m = seconds / 60
    if m < 10:
        return max(1, round(m))
    if m < 60:
        return round(m / 5) * 5
    if m < 360:
        return round(m / 10) * 10
    return round(m / 30) * 30


def _duration(mins: int, lang: str) -> str:
    h, m = divmod(mins, 60)
    if lang == "zh-TW":
        if h == 0:
            return f"約 {m} 分鐘"
        return f"約 {h} 小時" + (f" {m} 分" if m else "")
    if h == 0:
        return f"About {m} min"
    return f"About {h} h" + (f" {m} min" if m else "")


def format_eta(seconds: float, lang: str, now: Any) -> str:
    """Rough ETA wording (ETA-011): <1 min; minutes; 5-min steps to 1 h; 10-min to 6 h; 30-min beyond.
    Over 15 minutes the expected finish time is appended (with "tomorrow" when it is the next day)."""
    zh = lang == "zh-TW"
    if seconds < 60:
        return "不到 1 分鐘" if zh else "Less than a minute"
    text = _duration(_round_minutes(seconds), "zh-TW" if zh else "en")
    if seconds <= 15 * 60:
        return text
    from datetime import timedelta

    finish = now + timedelta(seconds=seconds)
    hhmm = finish.strftime("%H:%M")
    days = (finish.date() - now.date()).days
    if seconds >= 6 * 3600 or days >= 1:
        day = ("明天 " if zh else "tomorrow ") if days == 1 else ("" if days == 0 else finish.strftime("%m/%d "))
        return f"{text} · {day}{hhmm} 完成" if zh else f"{text} · done {day}{hhmm}"
    return f"{text} · 預計 {hhmm} 完成" if zh else f"{text} · done by {hhmm}"


def should_update(shown: float, new: float, state: str) -> bool:
    """Anti-jitter (ETA-012): rough changes < 10 % and precise changes < 3 s are not shown."""
    if state == "precise":
        return abs(new - shown) >= 3
    return shown == 0 or abs(new - shown) / shown >= 0.10


def slow_explanation(
    rate_obs: float, rate_prior: float, gpu_util: float, cpu_util: float, gpu_busy_external: bool
) -> str | None:
    """Honest note when much slower than the prior (ETA-013), from worker bottleneck telemetry."""
    if rate_prior <= 0 or rate_obs >= rate_prior / 2:
        return None
    if gpu_busy_external:
        return "eta.slow.gpu_busy"
    if gpu_util < 50 and cpu_util >= 90:
        return "eta.slow.decode_bottleneck"
    return "eta.slow.generic"


def display_eta(eta: dict[str, Any], status: str, waiting_reason: str | None = None) -> dict[str, Any]:
    """A paused operation shows "暫停中" and freezes its number (ETA-014)."""
    if status in ("paused", "pausing", "waiting"):
        return {"text_key": "eta.paused", "frozen": True, "p50": eta.get("p50"), "countdown": False}
    return {
        "text_key": f"eta.{eta.get('state', 'estimating')}",
        "frozen": False,
        "p50": eta.get("p50"),
        "countdown": eta.get("state") == "precise" or status == "running",
    }


class CascadeMix:
    """Share of items routed to the VLM, EW-averaged over observed batches (ETA-015)."""

    def __init__(self, prior_ratio: float, alpha: float = 0.2) -> None:
        self.ratio = prior_ratio
        self.alpha = alpha

    def observe(self, vlm: int, total: int) -> None:
        if total > 0:
            self.ratio += self.alpha * (vlm / total - self.ratio)

    def remaining_seconds(self, items: int, vlm_s: float, emb_s: float) -> float:
        return items * (self.ratio * vlm_s + (1 - self.ratio) * emb_s)


class ItemsEta:
    """items-based ETA (06 §5.5.2 / §5.5.5): batch-rate EWMA (tau 60 s) with relative error
    cv / sqrt(batches remaining) + drift delta, capped at 0.9."""

    def __init__(self, total: int, batch_size: int, tau: float = 60.0, delta: float = 0.1) -> None:
        self.total, self.batch_size, self.delta = total, batch_size, delta
        self.done = 0
        self._rate = Ewma(tau)

    def batch_done(self, items: int, seconds: float) -> None:
        self.done += items
        self._rate.add(items / seconds, seconds)

    def estimate(self) -> tuple[float, float, float]:
        rate = self._rate.mean or 0.0
        remaining = self.total - self.done
        if rate <= 0:
            return math.inf, math.inf, math.inf
        p50 = remaining / rate
        cv = math.sqrt(self._rate.var) / rate
        batches = max(1, math.ceil(remaining / self.batch_size))
        p10, p90 = interval(p50, cv / math.sqrt(batches) + self.delta)
        return p10, p50, p90


def queue_ahead(p50s: list[float]) -> float:
    """Queue wait = sum of the p50s of operations ahead in the same lane (ETA-017)."""
    return float(sum(p50s))
