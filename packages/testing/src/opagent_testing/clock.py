"""FakeClock (docs/09 §3.2): deterministic time; ``advance()`` fires due callbacks in order."""

from __future__ import annotations

import asyncio
import heapq
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass(order=True)
class _Timer:
    due: float
    seq: int
    callback: Callable[[], None] = field(compare=False)
    cancelled: bool = field(default=False, compare=False)

    def cancel(self) -> None:
        self.cancelled = True


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._start = start or datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
        self._t = 0.0
        self._timers: list[_Timer] = []
        self._seq = itertools.count()

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self._t)

    def monotonic(self) -> float:
        return self._t

    def call_later(self, delay: float, callback: Callable[[], None]) -> _Timer:
        t = _Timer(self._t + max(0.0, delay), next(self._seq), callback)
        heapq.heappush(self._timers, t)
        return t

    async def sleep(self, seconds: float) -> None:
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        def wake() -> None:
            if not fut.done():
                fut.set_result(None)

        self.call_later(seconds, wake)
        await fut

    def advance(self, seconds: float) -> None:
        end = self._t + seconds
        while self._timers and self._timers[0].due <= end:
            t = heapq.heappop(self._timers)
            if t.cancelled:
                continue
            self._t = t.due
            t.callback()
        self._t = end

    async def tick(self, seconds: float = 0.0) -> None:
        """Advance and let woken coroutines run."""
        self.advance(seconds)
        for _ in range(5):
            await asyncio.sleep(0)

    @property
    def pending(self) -> int:
        return sum(1 for t in self._timers if not t.cancelled)


class AutoClock(FakeClock):
    """FakeClock whose ``sleep`` advances time immediately and records the requested delay — for retry/backoff
    logic driven by real I/O (downloads), where nobody else is there to call ``advance``."""

    def __init__(self, start: datetime | None = None) -> None:
        super().__init__(start)
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.advance(seconds)
        await asyncio.sleep(0)
