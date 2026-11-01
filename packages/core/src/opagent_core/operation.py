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


class IllegalOpTransition(Exception):
    pass


_O = OpStatus
OP_TRANSITIONS: frozenset[tuple[OpStatus | None, OpStatus]] = frozenset(
    {
        (None, _O.queued),
        (_O.queued, _O.waiting),
        (_O.waiting, _O.running),
        (_O.queued, _O.running),
        (_O.running, _O.waiting),
        (_O.running, _O.pausing),
        (_O.pausing, _O.paused),
        (_O.paused, _O.queued),
        (_O.running, _O.cancelling),
        (_O.cancelling, _O.cancelled),
        (_O.queued, _O.cancelled),
        (_O.paused, _O.cancelled),
        (_O.running, _O.succeeded),
        (_O.running, _O.failed),
        (_O.failed, _O.queued),
        (_O.running, _O.interrupted),
        (_O.interrupted, _O.queued),
        (_O.interrupted, _O.cancelled),
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
        links = (
            [{"label_key": "operation.link.review", "route": f"/review?job={op.id}"}] if st == "awaiting_review" else []
        )
        op.result = {"summary_key": f"operation.job.{st}", "links": links}
        return ProjectionUpdate()
    if st == "applying":
        return ProjectionUpdate(
            Operation(
                id=f"{op.id}:apply", type="output.apply", status=OpStatus.queued, subject={"kind": "job", "id": op.id}
            )
        )
    if st == "failed" and op.status == OpStatus.running:
        op.transition(OpStatus.failed)
    return ProjectionUpdate()


def sweep_orphans(ops: list[Operation], now: datetime, timeout_s: float = HEARTBEAT_TIMEOUT_S) -> list[OpEvent]:
    """Running operations whose owner heartbeat is older than ``timeout_s`` become ``interrupted`` (06 §5.8)."""
    events: list[OpEvent] = []
    for op in ops:
        stale = op.heartbeat_at is not None and (now - op.heartbeat_at).total_seconds() > timeout_s
        if op.status == OpStatus.running and stale:
            op.transition(OpStatus.interrupted)
            events.append(OpEvent(op.id, "OWNER_LOST"))
    return events


# ---- progress (06 §5.4) / elapsed (06 §5.6) ----
JOB_LABEL_PHASES = (
    ("plan", 2),
    ("cache_lookup", 3),
    ("model_load", 5),
    ("embed", 10),
    ("infer", 75),
    ("postprocess", 5),
)


@dataclass
class Phase:
    id: str
    weight: float
    est_seconds: float | None = None
    status: str = "pending"
    fraction: float = 0.0


def phase_fraction(phases: list[Phase]) -> float:
    """Weighted progress: time weights when every active phase has an estimate, else the 06 §5.2 static
    weights; skipped phases drop out and their weight is redistributed (OP-002)."""
    active = [p for p in phases if p.status != "skipped"]
    if not active:
        return 1.0
    timed = all(p.est_seconds is not None for p in active)
    w = [(p.est_seconds or 0.0) if timed else p.weight for p in active]
    tot = sum(w)
    if tot <= 0:
        return 0.0
    got = sum(
        wi * (1.0 if p.status == "done" else p.fraction if p.status == "running" else 0.0)
        for wi, p in zip(w, active, strict=True)
    )
    return got / tot


def job_label_fraction(counters: dict[str, int], phase: str, skipped: tuple[str, ...] = ("model_load",)) -> float:
    """job.label progress; cache hits are accounted in ``cache_lookup`` so ``infer`` never jumps (OP-003)."""
    to_infer = max(1, counters["total"] - counters.get("cached", 0))
    infer_p = (counters.get("done", 0) + counters.get("failed", 0) + counters.get("skipped", 0)) / to_infer
    order = [p for p, _ in JOB_LABEL_PHASES]
    cur = order.index(phase)
    phases = []
    for i, (pid, w) in enumerate(JOB_LABEL_PHASES):
        st = "skipped" if pid in skipped else "done" if i < cur else "running" if i == cur else "pending"
        phases.append(Phase(pid, w, status=st, fraction=min(1.0, infer_p) if pid == "infer" else 0.0))
    return phase_fraction(phases)


def parent_fraction(children: list[tuple[float, float]]) -> float:
    """Parent progress = sum(child weight x child progress), weights = estimated seconds (OP-004)."""
    tot = sum(e for e, _ in children)
    return sum(e * f for e, f in children) / tot if tot > 0 else 0.0


class Elapsed:
    """active/paused time accounting; times are Clock.monotonic() seconds (OP-013)."""

    def __init__(self) -> None:
        self.active_ms = 0
        self.paused_ms = 0
        self._since: float | None = None
        self._paused_at: float | None = None

    def start(self, t: float) -> None:
        self._since = t

    def pause(self, t: float) -> None:
        if self._since is not None:
            self.active_ms += round((t - self._since) * 1000)
            self._since = None
        self._paused_at = t

    def resume(self, t: float) -> None:
        if self._paused_at is not None:
            self.paused_ms += round((t - self._paused_at) * 1000)
            self._paused_at = None
        self._since = t

    def finish(self, t: float) -> None:
        if self._since is not None:
            self.active_ms += round((t - self._since) * 1000)
            self._since = None

    @staticmethod
    def _dur(ms: int, lang: str) -> str:
        s = round(ms / 1000)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if lang == "zh-TW":
            parts = [f"{h} 小時" if h else "", f"{m} 分" if m else "", f"{sec} 秒" if sec else ""]
            text = " ".join(p for p in parts if p) or "0 秒"
            return text.replace(" 分", " 分鐘") if not sec and m and not h else text
        parts = [f"{h} h" if h else "", f"{m} min" if m else "", f"{sec} s" if sec else ""]
        return " ".join(p for p in parts if p) or "0 s"

    def summary(self, lang: str) -> str:
        took = self._dur(self.active_ms, lang)
        if lang == "zh-TW":
            note = f"（暫停 {self._dur(self.paused_ms, lang)}未計入）" if self.paused_ms else ""
            return f"花了 {took}{note}"
        note = f" ({self._dur(self.paused_ms, lang)} paused not counted)" if self.paused_ms else ""
        return f"Took {took}{note}"

    @staticmethod
    def clock_text(ms: int) -> str:
        s = ms // 1000
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
