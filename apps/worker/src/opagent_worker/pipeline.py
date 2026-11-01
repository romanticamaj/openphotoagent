"""Worker-local prefetch queue with backpressure (02 §4.3 背壓)."""

from __future__ import annotations

from typing import Any


class BatchQueue:
    def __init__(self, max_batches: int = 4) -> None:
        self.max_batches = max_batches

    def can_lease(self) -> bool:
        return True

    def put(self, batch: list[Any]) -> None:
        pass

    def take(self) -> list[Any]:
        return []
