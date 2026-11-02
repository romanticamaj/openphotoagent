"""Operation persistence and Job -> Operation projection (06 §5.1, §5.3, §5.8)."""

from __future__ import annotations

from typing import Any

from opagent_core.operation import OP_TRANSITIONS, OpStatus
from opagent_db.schema import event, operation, operation_event
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
        conn.execute(
            insert(operation).values(
                id=op_id, type=op_type, status="queued", subject=subject, seq=1, updated_at=now, heartbeat_at=now
            )
        )
        conn.execute(insert(operation_event).values(operation_id=op_id, seq=1, kind="operation.created", at=now))
        conn.execute(
            insert(event).values(
                kind="operation.created",
                at=now,
                payload={
                    "v": 1,
                    "id": op_id,
                    "seq": 1,
                    "type": op_type,
                    "status": "queued",
                    "server_time": now.isoformat(),
                },
            )
        )


def set_op_status(conn: Any, clock: Any, op_id: str, target: str, waiting_reason: str | None = None) -> None:
    row = conn.execute(
        select(operation.c.status, operation.c.seq, operation.c.type).where(operation.c.id == op_id).with_for_update()
    ).first()
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
    conn.execute(
        update(operation)
        .where(operation.c.id == op_id)
        .values(
            status=target,
            seq=seq,
            updated_at=now,
            waiting_reason=waiting_reason,
            finished_at=now if target in ("succeeded", "failed", "cancelled") else None,
        )
    )
    conn.execute(
        insert(operation_event).values(
            operation_id=op_id,
            seq=seq,
            kind="operation.status",
            payload={"status": target, "waiting_reason": waiting_reason},
            at=now,
        )
    )
    conn.execute(
        insert(event).values(
            kind="operation.status",
            at=now,
            payload={
                "v": 1,
                "id": op_id,
                "type": row.type,  # lets the desktop UI pick which finished work raises an OS notification
                "seq": seq,
                "status": target,
                "waiting_reason": waiting_reason,
                "server_time": now.isoformat(),
            },
        )
    )
    return None


def project_job(conn: Any, clock: Any, job_id: str, job_status: str) -> None:
    for target in _PROJECTION.get(job_status, []):
        set_op_status(conn, clock, job_id, target, "PAUSED_BY_USER" if target == "paused" else None)


PROGRESS_WRITE_S = 2.0  # 06 §5.8: snapshot at most every 2 s
SAMPLE_EVERY_S = 60.0  # one progress sample per minute in operation_event
RETENTION_OK_DAYS, RETENTION_FAILED_DAYS, RETENTION_MAX = 30, 90, 500


class OperationWriter:
    """Throttled progress persistence; status changes are written immediately (OP-005)."""

    def __init__(self, engine: Any, clock: Any) -> None:
        self.engine, self.clock = engine, clock
        self._last_write: dict[str, float] = {}
        self._last_sample: dict[str, float] = {}

    def progress(self, op_id: str, fraction: float, **extra: Any) -> int:
        t = self.clock.monotonic()
        if t - self._last_write.get(op_id, -1e9) < PROGRESS_WRITE_S:
            return 0
        self._last_write[op_id] = t
        with self.engine.begin() as c:
            row = c.execute(select(operation.c.seq, operation.c.progress).where(operation.c.id == op_id)).first()
            if row is None:
                return 0
            seq = row.seq + 1
            prog = {**(row.progress or {}), **extra, "fraction": fraction}
            c.execute(
                update(operation)
                .where(operation.c.id == op_id)
                .values(progress=prog, seq=seq, updated_at=self.clock.now())
            )
            if t - self._last_sample.get(op_id, -1e9) >= SAMPLE_EVERY_S:
                self._last_sample[op_id] = t
                c.execute(
                    insert(operation_event).values(
                        operation_id=op_id,
                        seq=seq,
                        kind="progress.sample",
                        payload={"fraction": fraction},
                        at=self.clock.now(),
                    )
                )
        return 1

    def status(self, op_id: str, target: str, waiting_reason: str | None = None) -> None:
        with self.engine.begin() as c:
            set_op_status(c, self.clock, op_id, target, waiting_reason)


def complete_operation(
    conn: Any,
    clock: Any,
    op_id: str,
    worker_id: str | None = None,
    variant: str | None = None,
    items: int = 0,
    seconds: float = 0.0,
    predicted_p50: float | None = None,
    predicted_p10: float | None = None,
    predicted_p90: float | None = None,
    bytes_total: int = 0,
) -> None:
    """Success: write perf_observation and eta_calibration (06 §5.8 觀測回寫, OP-015)."""
    from opagent_db.schema import eta_calibration, perf_observation

    op_type = conn.execute(select(operation.c.type).where(operation.c.id == op_id)).scalar_one()
    now = clock.now()
    conn.execute(
        insert(perf_observation).values(
            worker_id=worker_id,
            variant=variant,
            op_type=op_type,
            at=now,
            items_per_s=items / seconds if seconds > 0 and items else None,
            bytes_per_s=bytes_total / seconds if seconds > 0 and bytes_total else None,
        )
    )
    if predicted_p50:
        covered = None if predicted_p10 is None or predicted_p90 is None else predicted_p10 <= seconds <= predicted_p90
        conn.execute(
            insert(eta_calibration).values(
                operation_id=op_id,
                op_type=op_type,
                predicted_p50=predicted_p50,
                predicted_p10=predicted_p10,
                predicted_p90=predicted_p90,
                actual_s=seconds,
                ratio=seconds / predicted_p50,
                covered=covered,
                at=now,
            )
        )
    set_op_status(conn, clock, op_id, "succeeded")


def sweep_retention(conn: Any, clock: Any) -> int:
    """Succeeded/cancelled kept 30 days, failed 90 days, at most 500 finished operations (OP-007)."""
    from datetime import timedelta

    from sqlalchemy import delete

    now = clock.now()
    n = 0
    for statuses, days in ((("succeeded", "cancelled"), RETENTION_OK_DAYS), (("failed",), RETENTION_FAILED_DAYS)):
        ids = [
            r[0]
            for r in conn.execute(
                select(operation.c.id).where(
                    operation.c.status.in_(statuses), operation.c.finished_at < now - timedelta(days=days)
                )
            )
        ]
        n += _delete_ops(conn, ids)
    finished = [
        r[0]
        for r in conn.execute(
            select(operation.c.id)
            .where(operation.c.status.in_(("succeeded", "cancelled", "failed")))
            .order_by(operation.c.finished_at.desc(), operation.c.id.desc())
            .offset(RETENTION_MAX)
        )
    ]
    n += _delete_ops(conn, finished)
    _ = delete
    return n


def _delete_ops(conn: Any, ids: list[str]) -> int:
    from sqlalchemy import delete

    if not ids:
        return 0
    conn.execute(delete(operation_event).where(operation_event.c.operation_id.in_(ids)))
    conn.execute(delete(operation).where(operation.c.id.in_(ids)))
    return len(ids)


def op_problem(d: dict[str, Any]) -> dict[str, Any] | None:
    """The catalog problem behind a failed or waiting Operation, with its fixes (06 §4.3, §5): the same shape a
    preflight blocker has, so every surface renders the same ProblemCard."""
    from opagent_doctor.codes import CATALOG, problem

    if d.get("status") == "failed":
        res = d.get("result") or {}
        code = res.get("error_code")
        if code:
            return problem(str(code), dict(res.get("details") or {}))
    if d.get("status") == "waiting" and d.get("waiting_reason") in CATALOG:
        code = str(d["waiting_reason"])
        params = {"host": "huggingface.co"} if code.startswith("NET_") else {}
        return problem(code, params)
    return None


def op_dict(row: Any) -> dict[str, Any]:
    d = dict(row._mapping)
    d["auto_resumed"] = bool((d.get("progress") or {}).get("auto_resumed"))  # Activity Center: 「已自動繼續」
    if not d.get("error"):
        d["error"] = op_problem(d)
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in d.items()}
