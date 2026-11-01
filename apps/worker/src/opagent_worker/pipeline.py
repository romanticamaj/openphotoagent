"""Worker-local prefetch queue with backpressure (02 §4.3 背壓): stop leasing when 4 batches are waiting."""

from __future__ import annotations

from collections import deque
from typing import Any


class BatchQueue:
    def __init__(self, max_batches: int = 4) -> None:
        self.max_batches = max_batches
        self._q: deque[list[Any]] = deque()

    def can_lease(self) -> bool:
        return len(self._q) < self.max_batches

    def put(self, batch: list[Any]) -> None:
        self._q.append(batch)

    def take(self) -> list[Any]:
        return self._q.popleft()
