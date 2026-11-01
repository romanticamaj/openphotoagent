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


class IllegalTransition(Exception):
    pass


@dataclass(frozen=True)
class StatusEvent:
    kind: str
    from_status: JobStatus | None
    to_status: JobStatus


_S = JobStatus
JOB_TRANSITIONS: frozenset[tuple[JobStatus | None, JobStatus]] = frozenset(
    {
        (None, _S.queued),
        (_S.queued, _S.running),
        (_S.running, _S.paused),
        (_S.paused, _S.running),
        (_S.running, _S.awaiting_review),
        (_S.awaiting_review, _S.planning),
        (_S.running, _S.planning),
        (_S.planning, _S.awaiting_approval),
        (_S.awaiting_approval, _S.applying),
        (_S.applying, _S.completed),
        (_S.awaiting_approval, _S.completed),
        (_S.running, _S.failed),
        (_S.queued, _S.cancelled),
        (_S.running, _S.cancelling),
        (_S.paused, _S.cancelled),
        (_S.cancelling, _S.cancelled),
        (_S.completed, _S.undoing),
        (_S.undoing, _S.undone),
        (_S.failed, _S.queued),
    }
)


def transition(current: JobStatus | None, target: JobStatus) -> StatusEvent:
    """Validate a transition and return the ``job.status`` event to emit."""
    if (current, target) not in JOB_TRANSITIONS:
        raise IllegalTransition(f"{current} -> {target}")
    return StatusEvent(kind="job.status", from_status=current, to_status=target)
