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


# ---- Operation model (06 §5.1, §5.3) ----
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


_O = OpStatus
OP_TRANSITIONS: frozenset[tuple[OpStatus | None, OpStatus]] = frozenset(
    {
        (None, _O.queued), (_O.queued, _O.waiting), (_O.waiting, _O.running), (_O.queued, _O.running),
        (_O.running, _O.waiting), (_O.running, _O.pausing), (_O.pausing, _O.paused), (_O.paused, _O.queued),
        (_O.running, _O.cancelling), (_O.cancelling, _O.cancelled), (_O.queued, _O.cancelled),
        (_O.paused, _O.cancelled), (_O.running, _O.succeeded), (_O.running, _O.failed), (_O.failed, _O.queued),
        (_O.running, _O.interrupted), (_O.interrupted, _O.queued), (_O.interrupted, _O.cancelled),
    }
)
HEARTBEAT_TIMEOUT_S = 60.0


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
    subject: dict[str, str] | None = None
    seq: int = 0
    display_fraction: float = 0.0
    regressed: bool = False
    waiting_reason: str | None = None
    result: dict[str, Any] = field(default_factory=dict)

    def _bump(self) -> None:
        self.seq += 1

    def transition(self, target: OpStatus) -> None:
        if (self.status, target) not in OP_TRANSITIONS:
            raise IllegalOpTransition(f"{self.status} -> {target}")
        self.status = target
        self._bump()

    def update_progress(self, fraction: float, regressed: bool = False) -> None:
        """display_fraction never decreases, except for an explicit retry/rollback flagged ``regressed`` (§5.4)."""
        f = min(1.0, max(0.0, fraction))
        if regressed and f < self.display_fraction:
            self.display_fraction = f
            self.regressed = True
        else:
            self.display_fraction = max(self.display_fraction, f)
            self.regressed = False
        self._bump()


@dataclass
class ProjectionUpdate:
    new_operation: Operation | None = None


def project_job_status(op: Operation, job_status: object) -> ProjectionUpdate:
    """Job -> Operation projection (06 §5.1, CORE-SM-008)."""
    st = str(job_status)
    if st in ("awaiting_review", "awaiting_approval", "completed") and op.status == OpStatus.running:
        op.transition(OpStatus.succeeded)
        links = [{"label_key": "operation.link.review", "route": f"/review?job={op.id}"}] if st == "awaiting_review" else []
        op.result = {"summary_key": f"operation.job.{st}", "links": links}
        return ProjectionUpdate()
    if st == "applying":
        return ProjectionUpdate(Operation(id=f"{op.id}:apply", type="output.apply", status=OpStatus.queued,
                                          subject={"kind": "job", "id": op.id}))
    if st == "failed" and op.status == OpStatus.running:
        op.transition(OpStatus.failed)
    return ProjectionUpdate()


def sweep_orphans(ops: list[Operation], now: datetime, timeout_s: float = HEARTBEAT_TIMEOUT_S) -> list[OpEvent]:
    """Running operations whose owner heartbeat is older than ``timeout_s`` become ``interrupted`` (06 §5.8)."""
    events: list[OpEvent] = []
    for op in ops:
        if op.status == OpStatus.running and op.heartbeat_at is not None:
            if (now - op.heartbeat_at).total_seconds() > timeout_s:
                op.transition(OpStatus.interrupted)
                events.append(OpEvent(op.id, "OWNER_LOST"))
    return events
