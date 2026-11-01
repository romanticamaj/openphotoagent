"""Worker protocol /worker-api/v1 (docs/02 §5.3): register, lease (SKIP LOCKED), complete."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request, Response
from opagent_core.ids import uuid7
from opagent_core.planner import PlanItem, assign_collisions, plan, summarize
from opagent_core.state_machine import JobStatus
from opagent_db.schema import (
    asset,
    asset_location,
    job,
    journal_mirror,
    output_action,
    output_plan,
    source,
    worker,
    worker_task,
)
from pydantic import BaseModel
from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import queue as q
from .db import offset_minutes, set_job_status, to_aware
from .state import DEFAULT_WS, AppState

router = APIRouter()
LEASE_S = 120


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


class RegisterIn(BaseModel):
    worker_id: str
    name: str = "local"
    roots: list[dict[str, str]]


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
            .values(id=body.worker_id, name=body.name, roots=roots, last_heartbeat_at=st.clock.now())
            .on_conflict_do_update(
                index_elements=["id"], set_={"roots": roots, "status": "online", "last_heartbeat_at": st.clock.now()}
            )
        )
    return {"worker_id": body.worker_id}


def _lease_one(st: AppState, worker_id: str) -> dict[str, Any] | None:
    with st.engine.begin() as c:
        row = c.execute(
            text(
                "SELECT id FROM worker_task WHERE status='pending' AND (worker_id IS NULL OR worker_id=:w) "
                "ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED"
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
        if t.kind == "scan":
            set_job_status(c, t.job_id, JobStatus.running)
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


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, req: Request) -> dict[str, int]:
    st = _st(req)
    with st.engine.begin() as c:
        return {"extended": q.heartbeat(c, st.clock, body.worker_id, body.lease_ids)}


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
        handler = {"scan": _on_scan, "apply": _on_apply, "undo": _on_undo}.get(t.kind)
        if handler and body.ok:
            handler(st, c, t, body.result)
        elif handler and t.job_id and t.kind == "scan":
            set_job_status(c, t.job_id, JobStatus.failed, error=str(body.result.get("error")))
    return {"status": "ok"}


def _on_scan(st: AppState, c: Any, t: Any, result: dict[str, Any]) -> None:
    src_id = t.payload["source_id"]
    items: list[PlanItem] = []
    for e in result["entries"]:
        taken = datetime.fromisoformat(e["taken_at"]) if e.get("taken_at") else None
        mtime = datetime.fromisoformat(e["mtime"])
        aid = c.execute(
            pg_insert(asset)
            .values(
                id=uuid7(st.clock),
                workspace_id=DEFAULT_WS,
                content_hash=e["content_hash"],
                size_bytes=e["size"],
                taken_at=taken,
                taken_at_offset_min=offset_minutes(taken) if taken else None,
                date_source=e.get("date_source"),
            width=e.get("width"), height=e.get("height"), thumbhash=e.get("thumbhash"), phash=e.get("phash"),
            dhash=e.get("dhash"),
        )
            .on_conflict_do_update(index_elements=["workspace_id", "content_hash"], set_={"size_bytes": e["size"]})
            .returning(asset.c.id)
        ).scalar_one()
        c.execute(
            pg_insert(asset_location)
            .values(
                id=uuid7(st.clock),
                asset_id=aid,
                source_id=src_id,
                rel_path=e["rel"],
                mtime=mtime,
                mtime_offset_min=offset_minutes(mtime),
                size=e["size"],
            )
            .on_conflict_do_update(
                index_elements=["source_id", "rel_path"],
                set_={"asset_id": aid, "mtime": mtime, "size": e["size"], "missing": False},
            )
        )
        items.append(PlanItem(aid, e["rel"], e["content_hash"], taken, e.get("date_source"), mtime))
    c.execute(update(source).where(source.c.id == src_id).values(last_scan_at=st.clock.now()))
    j = c.execute(select(job).where(job.c.id == t.job_id)).first()
    params = j.params
    set_job_status(c, j.id, JobStatus.planning)
    target = params["target"]
    planned = assign_collisions(
        plan(items, params["preset"], target.get("sub_path", "").strip("/")), case_insensitive=True
    )  # target FS may be case-insensitive (OUT-EXE-010)
    pid = uuid7(st.clock)
    c.execute(insert(output_plan).values(id=pid, job_id=j.id, status="draft", summary=summarize(planned)))
    for i, a in enumerate(planned):
        c.execute(
            insert(output_action).values(
                id=uuid7(st.clock),
                plan_id=pid,
                seq=i,
                asset_id=a.asset_id,
                action="copy",
                src_alias=t.payload["root_alias"],
                src_rel=a.src_rel,
                dst_alias=target["root_alias"],
                dst_rel=a.dst_rel,
                src_hash=a.src_hash,
                date_source=a.date_source,
            )
        )
    set_job_status(c, j.id, JobStatus.awaiting_approval, plan_id=pid, counters={"total": len(planned), "done": 0})


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
