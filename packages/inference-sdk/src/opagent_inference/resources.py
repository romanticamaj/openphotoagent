"""ResourceManager: slots, LRU unload with refcounts, idle unload (03 §4.2)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class Slot(StrEnum):
    SMALL = "small"
    LARGE = "large"


class _Clock(Protocol):
    def monotonic(self) -> float: ...


class VramBudgetError(RuntimeError):
    """Not enough VRAM even after unloading every idle model (maps to VRAM_INSUFFICIENT)."""


@dataclass
class _Loaded:
    gb: float
    slot: Slot
    last_used: float
    order: int
    refs: int = 0


class ResourceManager:
    def __init__(
        self,
        budget_gb: float,
        clock: _Clock,
        idle_unload_s: float = 600,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.budget_gb = budget_gb
        self.clock = clock
        self.idle_unload_s = idle_unload_s
        self.on_event = on_event or (lambda _e: None)
        self.loaded: dict[str, _Loaded] = {}
        self._seq = 0

    @property
    def used_gb(self) -> float:
        return sum(m.gb for m in self.loaded.values())

    def _unload(self, model: str, reason: str) -> None:
        del self.loaded[model]
        self.on_event({"kind": "model.unloaded", "model": model, "reason": reason})

    def load(self, model: str, gb: float, slot: Slot) -> list[str]:
        """Load ``model``; returns the models unloaded to make room. Models in use (refcount > 0) are never
        unloaded; the large slot is evicted before the small one, then least-recently-used first."""
        now = self.clock.monotonic()
        if model in self.loaded:
            self.loaded[model].last_used = now
            return []
        idle = sorted(
            (m for m, s in self.loaded.items() if s.refs == 0),
            key=lambda m: (self.loaded[m].slot != Slot.LARGE, self.loaded[m].last_used, self.loaded[m].order),
        )
        victims: list[str] = []
        free = self.budget_gb - self.used_gb
        for m in idle:
            if free >= gb:
                break
            victims.append(m)
            free += self.loaded[m].gb
        if free < gb:
            raise VramBudgetError(f"{model} needs {gb} GB, only {free:.2f} GB can be freed")
        for m in victims:
            self._unload(m, "lru")
        self._seq += 1
        self.loaded[model] = _Loaded(gb=gb, slot=slot, last_used=now, order=self._seq)
        return victims

    def acquire(self, model: str) -> None:
        s = self.loaded[model]
        s.refs += 1
        s.last_used = self.clock.monotonic()

    def release(self, model: str) -> None:
        s = self.loaded[model]
        if s.refs <= 0:
            raise RuntimeError(f"release without acquire: {model}")
        s.refs -= 1

    def sweep_idle(self) -> list[str]:
        """Unload large-slot models idle for ``idle_unload_s`` (the small embedder stays resident)."""
        now = self.clock.monotonic()
        gone = [
            m
            for m, s in self.loaded.items()
            if s.slot == Slot.LARGE and s.refs == 0 and now - s.last_used >= self.idle_unload_s
        ]
        for m in gone:
            self._unload(m, "idle")
        return gone
