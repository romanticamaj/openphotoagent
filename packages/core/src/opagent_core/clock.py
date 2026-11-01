"""Clock protocol (docs/09 §3.2, CLAUDE.md invariant 11).

All product code obtains time through a :class:`Clock`. Only this module may
read the OS clock directly; tests use ``opagent_testing.clock.FakeClock``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol


class Handle(Protocol):
    def cancel(self) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware current time (UTC)."""
        ...

    def monotonic(self) -> float:
        """Monotonic seconds, for durations."""
        ...

    async def sleep(self, seconds: float) -> None: ...

    def call_later(self, delay: float, callback: Callable[[], None]) -> Handle: ...


class SystemClock:
    """Real clock used by production code."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def call_later(self, delay: float, callback: Callable[[], None]) -> Handle:
        return asyncio.get_running_loop().call_later(delay, callback)
