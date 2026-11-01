"""ETA estimation (docs/06 §5.5). Pure: callers pass timestamps (Clock), nothing reads time here."""

from __future__ import annotations

from typing import Any


class BytesRate:
    def __init__(self, tau_fast: float = 5.0, tau_slow: float = 30.0) -> None:
        self.fast = 0.0
        self.slow = 0.0

    def update(self, t: float, total: int) -> None:
        pass

    def pause(self, t: float) -> None:
        pass

    def resume(self, t: float) -> None:
        pass

    def speed(self, elapsed: float) -> float:
        return 0.0


def shrink(prior: float, basis: str, observed: float | None, n: int) -> float:
    return 0.0


def select_prior(
    same_sig: float | None, other_sig: tuple[float, int, int] | None, registry: float | None, gpu_factor: float = 1.0
) -> tuple[float | None, str]:
    return None, ""


def multi_phase(phases: list[tuple[float, float]]) -> tuple[float, float]:
    return 0.0, 0.0


def interval(p50: float, sigma_rel: float) -> tuple[float, float]:
    return 0.0, 0.0


class EtaTracker:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    def state(self, elapsed: float, samples: int, has_prior: bool, phase: str | None = None) -> str:
        return ""

    def update(
        self, t: float, p50: float, p10: float, p90: float, samples: int, has_prior: bool, observed: bool
    ) -> str:
        return ""

    def progress(self, t: float, done: int, batch_interval: float) -> None:
        pass

    def stalled_state(self, t: float, reason: str | None = None) -> dict[str, Any] | None:
        return {}
