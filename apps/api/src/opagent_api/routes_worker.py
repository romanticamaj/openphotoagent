"""Worker protocol /worker-api/v1 (docs/02 §5.3): register, lease (SKIP LOCKED), complete."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request, Response
from opagent_core.state_machine import JobStatus
from opagent_db.schema import (
    event,
    job,
    journal_mirror,
    output_action,
    output_plan,
    worker,
    worker_task,
)
from pydantic import BaseModel
from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import organize
from . import queue as q
from .db import set_job_status, to_aware
from .operations import ensure_operation, set_op_status
from .state import AppState

router = APIRouter()
TASK_OP_TYPES = {"scan": "scan", "apply": "output.apply", "undo": "output.undo"}
LEASE_S = 120


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


class RegisterIn(BaseModel):
    worker_id: str
    name: str = "local"
    roots: list[dict[str, str]]
    models: dict[str, str] = {}  # task -> model_fingerprint of the loaded/installed backend


class LeaseIn(BaseModel):
    worker_id: str
    wait_s: float = 0.0


class CompleteIn(BaseModel):
    result: dict[str, Any]
    ok: bool = True


@router.post("/register")
def register(body: RegisterIn, req: Request) -> dict[str, str]:
    roots = [{"alias": r["alias"], "mode": r.get("mode", "ro")} for r in body.roots]  # never absolute paths
    st = _st(req)
    with st.engine.begin() as c:
        c.execute(
            pg_insert(worker)
            .values(
                id=body.worker_id,
                name=body.name,
                roots=roots,
                last_heartbeat_at=st.clock.now(),
                capabilities={"models": body.models},
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "roots": roots,
                    "status": "online",
                    "last_heartbeat_at": st.clock.now(),
                    "capabilities": {"models": body.models},
                },
            )
        )
    return {"worker_id": body.worker_id}


def _lease_one(st: AppState, worker_id: str) -> dict[str, Any] | None:
    with st.engine.begin() as c:
        row = c.execute(
            text(
                "SELECT id FROM worker_task WHERE status='pending' AND (worker_id IS NULL OR worker_id=:w) "
                "ORDER BY (lane = 'interactive') DESC, created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED"
            ),
            {"w": worker_id},
        ).first()
        if row is None:
            return None
        t = c.execute(
            update(worker_task)
            .where(worker_task.c.id == row.id)
            .values(status="leased", worker_id=worker_id, lease_expires_at=st.clock.now() + timedelta(seconds=LEASE_S))
            .returning(worker_task)
        ).first()
        assert t is not None
        if t.kind in TASK_OP_TYPES:  # every long action is an Operation (06 §5, OP-001)
            ensure_operation(c, st.clock, t.id, TASK_OP_TYPES[t.kind], {"kind": "task", "id": t.id})
            set_op_status(c, st.clock, t.id, "running")
        if t.kind == "scan":
            if "scan_started_at" not in t.payload:
                payload = {**t.payload, "scan_started_at": st.clock.now().isoformat()}
                c.execute(update(worker_task).where(worker_task.c.id == t.id).values(payload=payload))
                t = c.execute(select(worker_task).where(worker_task.c.id == t.id)).one()
            js = c.execute(select(job.c.status).where(job.c.id == t.job_id)).scalar()
            if js == "queued":
                set_job_status(c, t.job_id, JobStatus.running, clock=st.clock)
        return {"id": t.id, "kind": t.kind, "payload": t.payload}


@router.post("/lease")
def lease(body: LeaseIn, req: Request) -> Any:
    st = _st(req)
    deadline = st.clock.monotonic() + body.wait_s
    while True:
        task = _lease_one(st, body.worker_id)
        if task is not None:
            return task
        if st.clock.monotonic() >= deadline:
            return Response(status_code=204)
        time.sleep(0.05)  # noqa: TID251 - long-poll inside a threadpool request handler


class JournalIn(BaseModel):
    worker_id: str
    entries: list[dict[str, Any]]


class LeasesIn(BaseModel):
    worker_id: str
    max_items: int = 16
    loaded_models: list[str] = []
    lanes: list[str] | None = None


@router.post("/leases")
def leases(body: LeasesIn, req: Request) -> Any:
    st = _st(req)
    with st.engine.begin() as c:
        items = q.lease(c, st.clock, body.worker_id, body.max_items, body.loaded_models, body.lanes)
    if not items:
        return Response(status_code=204)
    return {"items": items}


class HeartbeatIn(BaseModel):
    worker_id: str
    lease_ids: list[str] = []
    disk_free: dict[str, int] | None = None


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        if body.disk_free is not None:
            c.execute(
                update(worker)
                .where(worker.c.id == body.worker_id)
                .values(disk_free=body.disk_free, last_heartbeat_at=st.clock.now())
            )
        return {
            "extended": q.heartbeat(c, st.clock, body.worker_id, body.lease_ids),
            "abort": q.abort_list(c, body.worker_id, body.lease_ids),
        }


class ResultsIn(BaseModel):
    worker_id: str
    items: list[dict[str, Any]]


@router.post("/results")
def results(body: ResultsIn, req: Request) -> dict[str, int]:
    st = _st(req)
    with st.engine.begin() as c:
        return q.report(c, st.clock, body.worker_id, body.items)


@router.post("/release")
def release(body: HeartbeatIn, req: Request) -> dict[str, int]:
    with _st(req).engine.begin() as c:
        return {"released": q.release(c, body.worker_id, body.lease_ids)}


@router.post("/sweep")
def sweep(req: Request) -> dict[str, int]:
    st = _st(req)
    with st.engine.begin() as c:
        return {"expired": q.sweep(c, st.clock)}


@router.post("/journal")
def mirror_journal(body: JournalIn, req: Request) -> dict[str, int]:
    """Idempotent CP mirror of the worker journal (OUT-JRN-005). The worker copy is authoritative:
    re-delivered entries are ignored, never rewritten."""
    rows = [
        {
            "worker_id": body.worker_id,
            "plan_id": e["plan_id"],
            "seq": e["seq"],
            "action_id": e["action_id"],
            "op": e["op"],
            "phase": e["phase"],
            "args": e.get("args") or {},
            "pre_state": e.get("pre_state") or {},
            "post_state": e.get("post_state"),
        }
        for e in body.entries
    ]
    if not rows:
        return {"inserted": 0}
    with _st(req).engine.begin() as c:
        res = c.execute(
            pg_insert(journal_mirror)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["worker_id", "plan_id", "seq"])
        )
    return {"inserted": res.rowcount or 0}


class ProgressIn(BaseModel):
    events: list[dict[str, Any]] = []  # streamed stage events (ORG-008 try-one)
    entries: list[dict[str, Any]] = []
    cursor: str | None = None
    waiting_reason: str | None = None


@router.post("/tasks/{task_id}/progress")
def task_progress(task_id: str, body: ProgressIn, req: Request) -> dict[str, str]:
    """Scan pages (1,000 entries) with a resume cursor (SCAN-019); waiting states such as SOURCE_OFFLINE (SCAN-020)."""
    st = _st(req)
    with st.engine.begin() as c:
        t = c.execute(select(worker_task).where(worker_task.c.id == task_id).with_for_update()).first()
        if t is None or t.status != "leased":
            return {"status": "ignored"}
        for ev in body.events:
            c.execute(insert(event).values(kind="preview.stage", payload={**ev, "task_id": task_id}))
        if body.entries:
            organize.upsert_entries(st, c, t.payload["source_id"], body.entries)
        payload = dict(t.payload)
        if body.cursor is not None:
            payload["cursor"] = body.cursor
        c.execute(
            update(worker_task)
            .where(worker_task.c.id == task_id)
            .values(payload=payload, lease_expires_at=st.clock.now() + timedelta(seconds=LEASE_S))
        )
        if body.waiting_reason:
            set_op_status(c, st.clock, task_id, "waiting", body.waiting_reason)
        elif t.kind in TASK_OP_TYPES:
            set_op_status(c, st.clock, task_id, "running")
    return {"status": "ok"}


@router.post("/tasks/{task_id}/complete")
def complete(task_id: str, body: CompleteIn, req: Request) -> dict[str, str]:
    st = _st(req)
    with st.engine.begin() as c:
        t = c.execute(select(worker_task).where(worker_task.c.id == task_id).with_for_update()).first()
        if t is None or t.status != "leased":
            return {"status": "ignored"}  # idempotent re-delivery
        c.execute(
            update(worker_task)
            .where(worker_task.c.id == task_id)
            .values(status="done" if body.ok else "failed", result=body.result)
        )
        handler = {"scan": _on_scan, "apply": _on_apply, "undo": _on_undo, "estimate": _on_estimate}.get(t.kind)
        if t.kind in TASK_OP_TYPES:
            waiting = body.result.get("status") == "waiting"
            set_op_status(
                c,
                st.clock,
                t.id,
                "waiting" if waiting else "succeeded" if body.ok else "failed",
                body.result.get("waiting_reason") if waiting else None,
            )
        if handler and body.ok:
            handler(st, c, t, body.result)
        elif handler and t.job_id and t.kind == "scan":
            set_job_status(c, t.job_id, JobStatus.failed, error=str(body.result.get("error")))
    return {"status": "ok"}


def _on_scan(st: AppState, c: Any, t: Any, result: dict[str, Any]) -> None:
    organize.upsert_entries(st, c, t.payload["source_id"], result.get("entries") or [])
    organize.finish_scan(st, c, t)


def _on_estimate(st: AppState, c: Any, t: Any, result: dict[str, Any]) -> None:
    organize.finish_estimate(st, c, t.payload["job_id"], result)


def _on_apply(st: AppState, c: Any, t: Any, result: dict[str, Any]) -> None:
    pid = t.payload["plan_id"]
    done = 0
    for r in result["results"]:
        c.execute(
            update(output_action)
            .where(output_action.c.id == r["action_id"])
            .values(status=r["status"], error=r.get("error"), dst_rel=r.get("dst_rel") or output_action.c.dst_rel)
        )
        done += r["status"] == "done"
    total = len(result["results"])
    c.execute(
        update(output_plan)
        .where(output_plan.c.id == pid)
        .values(status="applied" if done == total else "partially_applied")
    )
    set_job_status(
        c,
        t.job_id,
        JobStatus.completed,
        finished_at=st.clock.now(),
        counters={"total": total, "done": done, "failed": total - done},
    )


def _on_undo(st: AppState, c: Any, t: Any, result: dict[str, Any]) -> None:
    pid = t.payload["plan_id"]
    for r in result["results"]:
        c.execute(
            update(output_action)
            .where(output_action.c.id == r["action_id"])
            .values(status="undone" if r["status"] == "undone" else r["status"], error=r.get("error"))
        )
    c.execute(update(output_plan).where(output_plan.c.id == pid).values(status="undone"))
    set_job_status(c, t.job_id, JobStatus.undone)


_ = to_aware  # re-exported for planners reading stored dates
