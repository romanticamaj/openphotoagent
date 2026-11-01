"""OperationReporter: minimal interface for long operations (docs/06 §5, 09 §7 slice 1).

Slice 6 fills in persistence, ETA and waiting reasons behind this same interface.
"""

from __future__ import annotations

from typing import Literal, Protocol

Unit = Literal["bytes", "items", "files", "steps"]


class OperationReporter(Protocol):
    def start(self, op_type: str, total: int | None = None, unit: Unit = "items") -> None: ...

    def phase(self, name: str) -> None: ...

    def progress(self, done: int, total: int | None = None) -> None: ...

    def finish(self, status: Literal["succeeded", "failed", "cancelled"]) -> None: ...


class NullReporter:
    def start(self, op_type: str, total: int | None = None, unit: Unit = "items") -> None:
        return None

    def phase(self, name: str) -> None:
        return None

    def progress(self, done: int, total: int | None = None) -> None:
        return None

    def finish(self, status: Literal["succeeded", "failed", "cancelled"]) -> None:
        return None
