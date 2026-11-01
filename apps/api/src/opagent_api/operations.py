"""Operation persistence and Job -> Operation projection (06 §5.1, §5.3, §5.8)."""

from __future__ import annotations

from typing import Any

from opagent_core.operation import OP_TRANSITIONS, OpStatus
from opagent_db.schema import operation, operation_event
from sqlalchemy import insert, select, update

# job status -> operation status path (06 §5.1: the Job is projected to an Operation 1:1)
_PROJECTION = {
    "queued": ["queued"],
    "running": ["running"],
    "paused": ["pausing", "paused"],
    "cancelling": ["cancelling"],
    "cancelled": ["cancelled"],
    "failed": ["failed"],
    "awaiting_review": ["succeeded"],
    "awaiting_approval": ["succeeded"],
    "completed": ["succeeded"],
}


def ensure_operation(conn: Any, clock: Any, op_id: str, op_type: str, subject: dict[str, str] | None = None) -> None:
    if conn.execute(select(operation.c.id).where(operation.c.id == op_id)).first() is None:
        now = clock.now()
        conn.execute(insert(operation).values(id=op_id, type=op_type, status="queued", subject=subject, seq=1,
                                              updated_at=now, heartbeat_at=now))
        conn.execute(insert(operation_event).values(operation_id=op_id, seq=1, kind="operation.created", at=now))


def set_op_status(conn: Any, clock: Any, op_id: str, target: str, waiting_reason: str | None = None) -> None:
    row = conn.execute(select(operation.c.status, operation.c.seq).where(operation.c.id == op_id)
                       .with_for_update()).first()
    if row is None or row.status == target:
        return
    cur = OpStatus(row.status)
    if (cur, OpStatus(target)) not in OP_TRANSITIONS:
        # paused -> running must go through queued (06 §5.3)
        if (cur, OpStatus.queued) in OP_TRANSITIONS and (OpStatus.queued, OpStatus(target)) in OP_TRANSITIONS:
            set_op_status(conn, clock, op_id, "queued")
            return set_op_status(conn, clock, op_id, target, waiting_reason)
        return None
    now = clock.now()
    seq = row.seq + 1
    conn.execute(update(operation).where(operation.c.id == op_id).values(
        status=target, seq=seq, updated_at=now, waiting_reason=waiting_reason,
        finished_at=now if target in ("succeeded", "failed", "cancelled") else None))
    conn.execute(insert(operation_event).values(operation_id=op_id, seq=seq, kind="operation.status",
                                                payload={"status": target, "waiting_reason": waiting_reason}, at=now))
    return None


def project_job(conn: Any, clock: Any, job_id: str, job_status: str) -> None:
    for target in _PROJECTION.get(job_status, []):
        set_op_status(conn, clock, job_id, target,
                      "PAUSED_BY_USER" if target == "paused" else None)
