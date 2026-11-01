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


# ---- Operation model (06 §5.1, §5.3) — stubs ----
from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime  # noqa: E402
from enum import StrEnum  # noqa: E402
from typing import Any  # noqa: E402


class OpStatus(StrEnum):
    queued = "queued"
    waiting = "waiting"
    running = "running"
    pausing = "pausing"
    paused = "paused"
    cancelling = "cancelling"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    interrupted = "interrupted"


class IllegalOpTransition(Exception):  # noqa: N818
    pass


OP_TRANSITIONS: frozenset[tuple[OpStatus | None, OpStatus]] = frozenset()


@dataclass
class OpEvent:
    op_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Operation:
    id: str
    type: str
    status: OpStatus | None = OpStatus.queued
    heartbeat_at: datetime | None = None
    parent_id: str | None = None
    seq: int = 0
    display_fraction: float = 0.0
    regressed: bool = False
    result: dict[str, Any] = field(default_factory=dict)

    def transition(self, target: OpStatus) -> None:
        raise IllegalOpTransition("stub")

    def update_progress(self, fraction: float, regressed: bool = False) -> None:
        pass


@dataclass
class ProjectionUpdate:
    new_operation: Operation | None = None


def project_job_status(op: Operation, job_status: object) -> ProjectionUpdate:
    return ProjectionUpdate()


def sweep_orphans(ops: list[Operation], now: datetime, timeout_s: float = 60.0) -> list[OpEvent]:
    return []
