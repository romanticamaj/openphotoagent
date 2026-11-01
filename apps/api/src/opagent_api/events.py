"""Event fan-out: SSE /events with Last-Event-ID replay and progress throttling (06 §5.7)."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from opagent_db.schema import event
from sqlalchemy import select

OUTBOX_RETENTION = timedelta(minutes=10)


class ProgressThrottle:
    """<= 1 Hz per operation, or immediately on a >= 1 % change; 0.2 Hz while the page is hidden (OP-010).
    Status events are never throttled."""

    def __init__(self, clock: Any) -> None:
        self.clock = clock
        self._last: dict[str, tuple[float, float]] = {}

    def should_send(self, op_id: str, fraction: float, visible: bool = True) -> bool:
        t = self.clock.monotonic()
        prev = self._last.get(op_id)
        if prev is None:
            ok = True
        elif visible:
            ok = abs(fraction - prev[1]) >= 0.01 or t - prev[0] >= 1.0
        else:
            ok = t - prev[0] >= 5.0
        if ok:
            self._last[op_id] = (t, fraction)
        return ok

    def should_send_status(self, op_id: str) -> bool:
        return True


def sse_chunk(eid: int, kind: str, payload: dict[str, Any]) -> str:
    return f"id: {eid}\nevent: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def replay(conn: Any, clock: Any, last_event_id: int | None) -> str:
    """Events after ``last_event_id`` still in the 10-minute outbox; ``reset`` when the client is too far behind
    (it then refetches GET /operations?status=active, OP-008)."""
    cutoff = clock.now() - OUTBOX_RETENTION
    if last_event_id is not None:
        at = conn.execute(select(event.c.at).where(event.c.id == last_event_id)).scalar()
        if at is None or at < cutoff:
            return sse_chunk(last_event_id, "reset", {"refetch": "/api/v1/operations?status=active"})
    q = select(event).where(event.c.at >= cutoff).order_by(event.c.id)
    if last_event_id is not None:
        q = q.where(event.c.id > last_event_id)
    return "".join(sse_chunk(r.id, r.kind, r.payload) for r in conn.execute(q))
