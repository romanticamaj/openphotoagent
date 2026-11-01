"""ResourceManager: slots, LRU unload, idle unload (03 §4.2)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class Slot(StrEnum):
    SMALL = "small"
    LARGE = "large"


class ResourceManager:
    def __init__(self, budget_gb: float, clock: Any, idle_unload_s: float = 600, on_event: Any = None) -> None:
        self.loaded: dict[str, Any] = {}

    def load(self, model: str, gb: float, slot: Slot) -> list[str]:
        return []

    def acquire(self, model: str) -> None: ...

    def release(self, model: str) -> None: ...

    def sweep_idle(self) -> list[str]:
        return []
