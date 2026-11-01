"""Job state machine (docs/02 §4.2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    paused = "paused"
    awaiting_review = "awaiting_review"
    planning = "planning"
    awaiting_approval = "awaiting_approval"
    applying = "applying"
    completed = "completed"
    failed = "failed"
    cancelling = "cancelling"
    cancelled = "cancelled"
    undoing = "undoing"
    undone = "undone"


class IllegalTransition(Exception):  # noqa: N818 - name from 09a CORE-SM-002
    pass


@dataclass(frozen=True)
class StatusEvent:
    kind: str
    from_status: JobStatus | None
    to_status: JobStatus


JOB_TRANSITIONS: frozenset[tuple[JobStatus | None, JobStatus]] = frozenset()


def transition(current: JobStatus | None, target: JobStatus) -> StatusEvent:
    raise IllegalTransition(f"{current} -> {target}")
