"""Token buckets for MCP calls (05 §3.7; 07 §5 faces). Pure: the API stores ``state()`` per token and kind."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Limit:
    burst: int  # bucket size
    per_window: int  # refill amount per window
    window_s: int


LIMITS = {
    "read": Limit(burst=30, per_window=120, window_s=60),
    "propose": Limit(burst=10, per_window=10, window_s=3600),
    "jobs": Limit(burst=10, per_window=10, window_s=60),
    "faces_find": Limit(burst=60, per_window=60, window_s=60),
    "faces_list": Limit(burst=30, per_window=30, window_s=60),
}
# a full bucket starts with burst + one window's allowance: 151st read inside a minute is refused (MCP-RATE-001)
MCP_JOBS_CONCURRENT = 2
MCP_JOB_MAX_ITEMS = 50_000
PLAN_MAX_ACTIONS = 10_000
GPU_SECONDS_PER_DAY = 600


def kind_for(tool: str) -> str:
    return {
        "propose_organize": "propose",
        "run_labeling_job": "jobs",
        "find_photos_of_person": "faces_find",
        "list_people": "faces_list",
    }.get(tool, "read")


class Bucket:
    def __init__(self, kind: str, tokens: float, at: float) -> None:
        self.kind, self.tokens, self.at = kind, tokens, at
        self.limit = LIMITS[kind]

    @classmethod
    def new(cls, kind: str, *, now: datetime) -> Bucket:
        lim = LIMITS[kind]
        cap = lim.burst + lim.per_window if kind == "read" else lim.burst
        return cls(kind, float(cap), now.timestamp())

    @classmethod
    def from_state(cls, kind: str, state: dict[str, Any]) -> Bucket:
        return cls(kind, float(state["tokens"]), float(state["at"]))

    def state(self) -> dict[str, Any]:
        return {"tokens": self.tokens, "at": self.at}

    def _cap(self) -> int:
        return self.limit.burst + self.limit.per_window if self.kind == "read" else self.limit.burst

    def take(self, *, now: datetime) -> tuple[bool, int]:
        """(allowed, retry_after_s)."""
        t = now.timestamp()
        rate = self.limit.per_window / self.limit.window_s
        self.tokens = min(float(self._cap()), self.tokens + max(0.0, t - self.at) * rate)
        self.at = t
        if self.tokens >= 1:
            self.tokens -= 1
            return True, 0
        return False, max(1, int((1 - self.tokens) / rate + 0.999))
