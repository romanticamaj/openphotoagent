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


class PlanStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    applying = "applying"
    applied = "applied"
    partially_applied = "partially_applied"
    undoing = "undoing"
    undone = "undone"
    failed = "failed"


PLAN_TRANSITIONS: frozenset[tuple[PlanStatus, PlanStatus]] = frozenset()


def plan_transition(cur: PlanStatus, target: PlanStatus) -> PlanStatus:
    raise IllegalTransition(f"{cur} -> {target}")


class JobMachine:
    def __init__(self, status: JobStatus | None) -> None:
        self.status = status

    def to(self, target: JobStatus, active_leases: int = 0) -> StatusEvent:
        raise IllegalTransition("stub")


def retry_failed_items(m: JobMachine, items: dict[str, str]) -> dict[str, str]:
    return items


def complete_without_output(m: JobMachine, plan: dict[str, object]) -> dict[str, object]:
    return plan


def auto_pause_check(m: JobMachine, samples: int, failures: int) -> str | None:
    return None
