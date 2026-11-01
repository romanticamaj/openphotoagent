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


_P = PlanStatus
PLAN_TRANSITIONS: frozenset[tuple[PlanStatus, PlanStatus]] = frozenset(
    {
        (_P.draft, _P.approved),
        (_P.approved, _P.applying),
        (_P.applying, _P.applied),
        (_P.applying, _P.partially_applied),  # cancelled mid-apply (CORE-SM-009, OUT-EXE-017)
        (_P.applying, _P.failed),
        (_P.applied, _P.undoing),
        (_P.partially_applied, _P.undoing),
        (_P.undoing, _P.undone),
        (_P.applied, _P.undone),
    }
)


def plan_transition(cur: PlanStatus, target: PlanStatus) -> PlanStatus:
    if (cur, target) not in PLAN_TRANSITIONS:
        raise IllegalTransition(f"plan {cur} -> {target}")
    return target


class JobMachine:
    """Stateful wrapper: an illegal transition raises and leaves the status unchanged (CORE-SM-002)."""

    def __init__(self, status: JobStatus | None) -> None:
        self.status = status
        self.events: list[StatusEvent] = []

    def to(self, target: JobStatus, active_leases: int = 0) -> StatusEvent:
        if self.status == JobStatus.cancelling and target == JobStatus.cancelled and active_leases > 0:
            raise IllegalTransition(f"cancelling -> cancelled with {active_leases} active leases")
        ev = transition(self.status, target)
        self.status = target
        self.events.append(ev)
        return ev


def retry_failed_items(m: JobMachine, items: dict[str, str]) -> dict[str, str]:
    """failed -> queued: only failed items go back to pending; done/cached results are kept (CORE-SM-004)."""
    m.to(JobStatus.queued)
    return {k: ("pending" if v == "failed" else v) for k, v in items.items()}


def complete_without_output(m: JobMachine, plan: dict[str, object]) -> dict[str, object]:
    """awaiting_approval -> completed without applying: the plan stays draft and is marked discarded."""
    m.to(JobStatus.completed)
    return {**plan, "status": "draft", "discarded": True}


ERROR_RATE_THRESHOLD = 0.20
ERROR_RATE_MIN_SAMPLES = 50


def auto_pause_check(m: JobMachine, samples: int, failures: int) -> str | None:
    """02 §4.3: failure rate > 20 % with more than 50 samples pauses the job (waiting_reason PAUSED_ERROR_RATE)."""
    if samples > ERROR_RATE_MIN_SAMPLES and failures / samples > ERROR_RATE_THRESHOLD:
        m.to(JobStatus.paused)
        return "PAUSED_ERROR_RATE"
    return None
