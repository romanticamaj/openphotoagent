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
DRIFT_MIN, DRIFT_MAX = 0.04, 0.1  # 06 §5.5.5


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


def sigma_rel(cv: float, remaining: int, done: int, dt: float, tau: float, p50: float) -> float:
    """Relative error of a remaining time (06 §5.5.5, ADR-0028): noise over what is left, error in the rate itself
    (an EWMA remembers about (2-a)/a samples), and drift, which only has the time left to act in."""
    a = 1 - math.exp(-dt / tau) if dt > 0 else 1.0
    n_eff = max(1.0, min(float(done), (2 - a) / a))
    drift = min(DRIFT_MAX, max(DRIFT_MIN, DRIFT_MAX * math.sqrt(p50 / 3600)))
    return cv * math.sqrt(1 / max(1, remaining) + 1 / n_eff) + drift


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
    """items-based ETA (06 §5.5.2 / §5.5.5): batch-rate EWMA (tau 60 s), relative error from sigma_rel."""

    def __init__(self, total: int, batch_size: int, tau: float = 60.0) -> None:
        self.total, self.batch_size, self.tau = total, batch_size, tau
        self.done = 0
        self._batches = 0
        self._seconds = 0.0
        self._rate = Ewma(tau)

    def batch_done(self, items: int, seconds: float) -> None:
        self.done += items
        self._batches += 1
        self._seconds += seconds
        self._rate.add(items / seconds, seconds)

    def estimate(self) -> tuple[float, float, float]:
        rate = self._rate.mean or 0.0
        remaining = self.total - self.done
        if rate <= 0:
            return math.inf, math.inf, math.inf
        p50 = remaining / rate
        cv = math.sqrt(self._rate.var) / rate
        batches = max(1, math.ceil(remaining / self.batch_size))
        dt = self._seconds / max(1, self._batches)
        p10, p90 = interval(p50, sigma_rel(cv, batches, self._batches, dt, self.tau, p50))
        return p10, p50, p90


def queue_ahead(p50s: list[float]) -> float:
    """Queue wait = sum of the p50s of operations ahead in the same lane (ETA-017)."""
    return float(sum(p50s))


MIN_SAMPLE_S = 0.5  # a download reports every 250 ms: too close together to be a rate
RATE_TAU = {"items": 60.0, "bytes": 30.0}  # 06 §5.5.1, §5.5.2


def eta_active_s(eta: dict[str, Any] | None, t: float) -> float:
    """Seconds the operation has really been working: suspended stretches do not count."""
    if not eta:
        return 0.0
    last = eta.get("_t")
    return float(eta.get("active_s", 0.0)) + (t - last if last is not None else 0.0)


def suspend_eta(eta: dict[str, Any], t: float) -> dict[str, Any]:
    """Paused or waiting: the clock stops, and the next report starts a new rate sample (ETA-021)."""
    return {**eta, "active_s": eta_active_s(eta, t), "_t": None, "_pt": None}


def seed_eta(prev: dict[str, Any] | None, t_start: float) -> dict[str, Any]:
    """The baseline at the operation's start (0 done), so the first report is already a rate sample (ETA-029); an
    estimate that is already running is left as it is."""
    prev = dict(prev or {})
    if prev.get("_pt") is not None or prev.get("samples"):
        return prev
    return {
        **prev,
        "_pt": t_start,
        "_pf": 0.0,
        "_t": t_start,
        "active_s": 0.0,
        "state": "estimating",
        "samples": 0,
        "_seeded": True,  # the first sample also holds the scan and the model loading (ETA-033)
    }


def live_eta(prev: dict[str, Any] | None, t: float, fraction: float | None, unit: str = "items") -> dict[str, Any]:
    """One progress report in, the next ``operation.eta`` out. The state is plain JSON kept on the operation, so it
    survives a restart and needs no process holding it. Keys with a leading underscore are working state."""
    prev = dict(prev or {})
    base: dict[str, Any] = {
        **prev,
        "active_s": eta_active_s(prev, t),
        "_t": t,
        "samples": prev.get("samples", 0),
        "state": prev.get("state", "estimating"),
    }
    if fraction is None:
        return {**base, "state": "unknown"}
    if prev.get("_pt") is None:
        return {**base, "_pt": t, "_pf": fraction}
    dt, df = t - prev["_pt"], fraction - prev["_pf"]
    if dt < MIN_SAMPLE_S:
        return base
    kind = "bytes" if unit == "bytes" else "items"
    tracker = EtaTracker(kind)
    if df <= 0:
        tracker.progress(prev["_pt"], 0, prev.get("_iv") or 0.0)
        stalled = tracker.stalled_state(t)
        return {**base, "state": "stalled", "since_s": stalled["since_s"]} if stalled else base

    rate = Ewma(RATE_TAU[kind])
    rate.mean, rate.var = prev.get("_mean"), prev.get("_var", 0.0)
    if prev.get("_seeded") and prev.get("samples", 0) == 1:
        # the second sample is the first with photos flowing: it replaces the start-up one (ETA-033)
        rate.mean, rate.var = None, 0.0
        prev = {k: v for k, v in prev.items() if k != "_seeded"}
        base.pop("_seeded", None)
    mean = rate.add(df / dt, dt)
    iv = dt if prev.get("_iv") is None else 0.8 * prev["_iv"] + 0.2 * dt
    out = {**base, "_pt": t, "_pf": fraction, "_mean": mean, "_var": rate.var, "_iv": iv}
    out["samples"] = samples = base["samples"] + 1
    out.pop("since_s", None)
    state = tracker.state(out["active_s"], samples, False)
    if state != "rough":
        return {**out, "state": state}

    remaining = max(0.0, 1.0 - fraction)
    p50 = remaining / mean
    batches = max(1, math.ceil(remaining / (mean * iv)))
    p10, p90 = interval(p50, sigma_rel(math.sqrt(rate.var) / mean, batches, samples, iv, RATE_TAU[kind], p50))
    tracker.current = prev.get("state", "rough")
    tracker._eligible_since = prev.get("_since")
    out["state"] = tracker.update(t, p50, p10, p90, samples, False, True)
    out["_since"] = tracker._eligible_since
    out.update(p50_s=p50, p10_s=p10, p90_s=p90)
    if "first" not in out:  # what the user was first told, as a total: this is what V7 judges (06 §5.8)
        a = out["active_s"]
        out["first"] = {"p50_s": a + p50, "p10_s": a + p10, "p90_s": a + p90}
    return out
