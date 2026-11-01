"""DB helpers for the API (sync SQLAlchemy Core)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from opagent_core.state_machine import JobStatus, transition
from opagent_db.schema import event, job
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection


def set_job_status(conn: Connection, job_id: str, target: JobStatus, **extra: Any) -> None:
    cur = conn.execute(select(job.c.status).where(job.c.id == job_id).with_for_update()).scalar_one()
    ev = transition(JobStatus(cur), target)
    conn.execute(update(job).where(job.c.id == job_id).values(status=target.value, **extra))
    conn.execute(insert(event).values(kind=ev.kind, payload={"job_id": job_id, "from": cur, "to": target.value}))


def to_aware(ts: datetime | None, offset_min: int | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(timezone(timedelta(minutes=offset_min or 0)))


def offset_minutes(dt: datetime) -> int:
    off = dt.utcoffset()
    return int(off.total_seconds() // 60) if off is not None else 0
