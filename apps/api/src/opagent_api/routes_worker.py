"""Worker protocol /worker-api/v1 (docs/02 §5.3): register, lease (SKIP LOCKED), complete."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from opagent_core.state_machine import JobStatus
from opagent_db.schema import (
    event,
    job,
    job_item,
    journal_mirror,
    operation,
    output_action,
    output_plan,
    source,
    worker,
    worker_task,
)
from pydantic import BaseModel
from sqlalchemy import false, func, insert, or_, select, text, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import organize
from . import queue as q
from .db import set_job_status, to_aware
from .operations import ensure_operation, observe_job, observe_progress, set_op_status
from .state import AppState

router = APIRouter()
TASK_OP_TYPES = {
    "scan": "scan",
    "apply": "output.apply",
    "undo": "output.undo",
    "download": "model.download",
    "remove": "model.remove",
    "benchmark": "benchmark.run",
    "embed_assets": "search.reindex",
}
LEASE_S = 120


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


class RegisterIn(BaseModel):
    worker_id: str
    name: str = "local"
    roots: list[dict[str, str]]
    models: dict[str, str] = {}  # task -> model_fingerprint of the loaded/installed backend
    hw: dict[str, Any] | None = None  # doctor's hardware summary (compat input, 06 §1.2)
    protocol_version: str = "1.0"
    deployment: str = "desktop"
    gpu_requested: bool = False
    doctor: list[dict[str, Any]] = []  # failing/warning doctor results, as consumed by preflight
    doctor_results: list[dict[str, Any]] = []  # full results (06 §3.5)
    roots_status: dict[str, str] = {}  # alias -> ok | denied | unavailable
    packs_status: dict[str, Any] = {}
    disk_free: dict[str, int] | None = None
    boot_id: str | None = None  # one per worker process: a new value means the previous process is gone
    app_version: str | None = None  # the product version the worker was built as (desktop bundles must match)


class LeaseIn(BaseModel):
    worker_id: str
    wait_s: float = 0.0
    exclude_kinds: list[str] = []
    boot_id: str | None = None  # e.g. "download" while the worker's download slot is busy (06 §2.4)


class CompleteIn(BaseModel):
    result: dict[str, Any]
    ok: bool = True


@router.post("/register")
def register(body: RegisterIn, req: Request) -> dict[str, Any]:
    roots = [{"alias": r["alias"], "mode": r.get("mode", "ro")} for r in body.roots]  # never absolute paths
    st = _st(req)
    caps = {
        "models": body.models,
        "hw": body.hw,
        "protocol_version": body.protocol_version,
        "deployment": body.deployment,
        "gpu_requested": body.gpu_requested,
        "doctor": body.doctor,
        "roots_status": body.roots_status,
        "packs_status": body.packs_status,
        "disk_free": body.disk_free or {},
        "doctor_results": body.doctor_results,
        "boot_id": body.boot_id,
        "app_version": body.app_version,
    }
    with st.engine.begin() as c:
        prev = c.execute(select(worker.c.capabilities).where(worker.c.id == body.worker_id)).scalar() or {}
        before = {r["check_id"]: r["status"] for r in prev.get("doctor_results") or []}
        after = {r["check_id"]: r["status"] for r in body.doctor_results}
        changed = sorted(k for k in after if k in before and before[k] != after[k])
        # leases held by any other process of this worker are orphaned (restart, crash, kill -9)
        new_process = body.boot_id is None or prev.get("boot_id") != body.boot_id
        _recover_downloads(c, st, body.worker_id, body.boot_id)
        _recover_jobs(c, st, body.worker_id, body.boot_id, interrupt_running=new_process and bool(prev))
        if changed:  # preflight results depending on these checks must be recomputed (DOC-019)
            c.execute(
                insert(event).values(kind="doctor.changed", payload={"worker_id": body.worker_id, "checks": changed})
            )
        c.execute(
            pg_insert(worker)
            .values(
                id=body.worker_id,
                name=body.name,
                roots=roots,
                last_heartbeat_at=st.clock.now(),
                capabilities=caps,
                disk_free=body.disk_free,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "roots": roots,
                    "status": "online",
                    "last_heartbeat_at": st.clock.now(),
                    "capabilities": caps,
                    "disk_free": body.disk_free,
                },
            )
        )
    from .routes_remote import welcome

    with st.engine.begin() as c:
        return {"worker_id": body.worker_id, "welcome": welcome(st, c, body.protocol_version)}


def _recover_downloads(c: Any, st: AppState, worker_id: str, boot: str | None = None) -> None:
    """A (re)registering worker lost its in-flight downloads (crash, kill -9): mark them interrupted and re-queue
    them; the downloader resumes from its .part files (06 §2.2, §5.8; DL-014)."""
    from opagent_db.schema import operation

    rows = c.execute(
        select(worker_task.c.id).where(
            worker_task.c.worker_id == worker_id,
            worker_task.c.kind == "download",
            worker_task.c.status == "leased",
            or_(
                true() if boot is None else false(),
                worker_task.c.lease_boot.is_(None),
                worker_task.c.lease_boot != boot,
            ),
        )
    ).all()
    for (tid,) in rows:
        c.execute(update(worker_task).where(worker_task.c.id == tid).values(status="pending", lease_expires_at=None))
        op = c.execute(select(operation.c.status, operation.c.progress).where(operation.c.id == tid)).first()
        if op is None:
            continue
        if op.status == "running":
            set_op_status(c, st.clock, tid, "interrupted")
        set_op_status(c, st.clock, tid, "queued")
        c.execute(
            update(operation)
            .where(operation.c.id == tid)
            .values(progress={**(op.progress or {}), "auto_resumed": True})
        )


def _recover_jobs(
    c: Any, st: AppState, worker_id: str, boot: str | None = None, interrupt_running: bool = False
) -> None:
    """Items the previous process had leased are lost: give them back and pause their jobs as interrupted, so the
    person sees what stopped and continues it (06 §5.3 interrupted, invariant 9; OP-006)."""
    from opagent_db.schema import job_item

    from .operations import ensure_operation

    rows = c.execute(
        select(job_item.c.id, job_item.c.job_id).where(
            job_item.c.lease_owner == worker_id,
            job_item.c.status.in_(("leased", "running")),
            or_(true() if boot is None else false(), job_item.c.lease_boot.is_(None), job_item.c.lease_boot != boot),
        )
    ).all()
    jobs = {r.job_id for r in rows}
    if rows:
        q.release(c, worker_id, [r.id for r in rows])
    if interrupt_running:  # a new process: its running jobs stopped with the old one, leased or not
        from opagent_db.schema import source

        jobs |= {
            j
            for (j,) in c.execute(
                select(job.c.id)
                .join(source, source.c.id == job.c.params["source_id"].astext)
                .where(job.c.status == "running", source.c.worker_id == worker_id)
            )
        }
    for jid in sorted(jobs):
        status = c.execute(select(job.c.status).where(job.c.id == jid)).scalar()
        if status != "running":
            continue
        ensure_operation(c, st.clock, jid, "job.label", {"kind": "job", "id": jid})
        set_op_status(c, st.clock, jid, "running")
        set_op_status(c, st.clock, jid, "interrupted")
        set_job_status(c, jid, JobStatus.paused, clock=st.clock)


def _lease_one(
    st: AppState, worker_id: str, exclude: list[str] | None = None, boot: str | None = None
) -> dict[str, Any] | None:
    with st.engine.begin() as c:
        c.execute(
            update(worker).where(worker.c.id == worker_id).values(last_heartbeat_at=st.clock.now(), status="online")
        )
        row = c.execute(
            text(
                "SELECT id FROM worker_task WHERE status='pending' AND (worker_id IS NULL OR worker_id=:w) "
                "AND NOT (kind = ANY(CAST(:ex AS text[]))) "
                "ORDER BY (kind = 'face_purge') DESC, (lane = 'interactive') DESC, created_at, id "
                "LIMIT 1 FOR UPDATE SKIP LOCKED"
            ),
            {"w": worker_id, "ex": list(exclude or [])},
        ).first()
        if row is None:
            return None
        t = c.execute(
            update(worker_task)
            .where(worker_task.c.id == row.id)
            .values(
                status="leased",
                worker_id=worker_id,
                lease_boot=boot,
                lease_expires_at=st.clock.now() + timedelta(seconds=LEASE_S),
            )
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
    if st.multi_user:  # a worker too old for this control plane gets no work (MU-WORKER-005)
        from .routes_remote import compatible

        with st.engine.begin() as c:
            if not compatible(st, c, body.worker_id):
                raise HTTPException(426, detail={"code": "UPGRADE_REQUIRED"})
    deadline = st.clock.monotonic() + body.wait_s
    while True:
        task = _lease_one(st, body.worker_id, body.exclude_kinds, body.boot_id)
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
    boot_id: str | None = None
    paused_reason: str | None = None  # the worker's resource policy (battery, GPU busy, not idle; 06 §5)
    max_items: int = 16
    loaded_models: list[str] = []
    lanes: list[str] | None = None


POLICY_REASONS = ("PAUSED_ON_BATTERY", "PAUSED_GPU_CONTENDED", "PAUSED_NOT_IDLE")


def _policy_wait(c: Any, st: AppState, reason: str | None) -> None:
    """Running label jobs wait with the worker's reason; when it clears they run again (06 §5, DESK-023)."""
    from opagent_db.schema import job_item, operation

    if reason:
        busy = select(job_item.c.job_id).where(job_item.c.status == "pending").distinct()
        for (oid,) in c.execute(
            select(operation.c.id).where(
                operation.c.type == "job.label", operation.c.status == "running", operation.c.id.in_(busy)
            )
        ).all():
            set_op_status(c, st.clock, oid, "waiting", reason)
    else:
        for (oid,) in c.execute(
            select(operation.c.id).where(
                operation.c.type == "job.label",
                operation.c.status == "waiting",
                operation.c.waiting_reason.in_(POLICY_REASONS),
            )
        ).all():
            set_op_status(c, st.clock, oid, "running")


@router.post("/leases")
def leases(body: LeasesIn, req: Request) -> Any:
    st = _st(req)
    with st.engine.begin() as c:
        _policy_wait(c, st, body.paused_reason if body.paused_reason in POLICY_REASONS else None)
        if body.paused_reason in POLICY_REASONS:
            return Response(status_code=204)
        items = q.lease(c, st.clock, body.worker_id, body.max_items, body.loaded_models, body.lanes, body.boot_id)
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
        out = q.report(c, st.clock, body.worker_id, body.items)
        ids = [i["job_item_id"] for i in body.items]
        for jid in c.execute(select(job_item.c.job_id).where(job_item.c.id.in_(ids)).distinct()).scalars():
            observe_job(c, st.clock, jid)
        return out


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
    progress: dict[str, Any] | None = None  # {done, total, unit, ...} for byte/item progress (downloads)
    events: list[dict[str, Any]] = []  # streamed stage events (ORG-008 try-one)
    entries: list[dict[str, Any]] = []
    cursor: str | None = None
    waiting_reason: str | None = None


@router.post("/tasks/{task_id}/progress")
def task_progress(task_id: str, body: ProgressIn, req: Request) -> dict[str, Any]:
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

        op = c.execute(select(operation.c.status).where(operation.c.id == task_id)).first()
        op_status = op.status if op else None
        if body.progress is not None and op is not None:
            keep = c.execute(select(operation.c.progress).where(operation.c.id == task_id)).scalar() or {}
            sticky = {k: keep[k] for k in ("auto_resumed",) if k in keep}  # 「已自動繼續」 outlives new progress
            c.execute(update(operation).where(operation.c.id == task_id).values(progress={**body.progress, **sticky}))
            if op_status not in ("paused", "pausing") and not body.waiting_reason:
                observe_progress(c, st.clock, task_id, body.progress)
        if body.waiting_reason:
            set_op_status(c, st.clock, task_id, "waiting", body.waiting_reason)
            if body.waiting_reason == "SOURCE_OFFLINE" and t.payload.get("source_id"):
                c.execute(update(source).where(source.c.id == t.payload["source_id"]).values(status="offline"))
        elif t.kind in TASK_OP_TYPES and op_status not in ("paused", "pausing", "cancelling", "cancelled"):
            set_op_status(c, st.clock, task_id, "running")
    # control channel: the worker checks this after each progress report (06 §2.2 true pause/cancel)
    control = {"paused": "pause", "pausing": "pause", "cancelling": "cancel", "cancelled": "cancel"}.get(
        op_status or ""
    )
    return {"status": "ok", "control": control}


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
        from .routes_bench import on_benchmark_complete
        from .routes_caption import on_captions_embedded
        from .routes_models import on_download_complete, on_remove_complete

        handler = {
            "scan": _on_scan,
            "apply": _on_apply,
            "undo": _on_undo,
            "estimate": _on_estimate,
            "download": on_download_complete,
            "remove": on_remove_complete,
            "benchmark": on_benchmark_complete,
            "embed_captions": on_captions_embedded,
        }.get(t.kind)
        if t.kind in TASK_OP_TYPES:
            waiting = body.result.get("status") == "waiting"
            rs = body.result.get("status")
            if rs == "paused" and body.result.get("waiting_reason"):  # paused_auto, e.g. disk full (DL-011)
                set_op_status(c, st.clock, t.id, "waiting", body.result["waiting_reason"])
            elif rs == "paused":  # resume re-queues the task and the download continues from its .part
                set_op_status(c, st.clock, t.id, "paused", "PAUSED_BY_USER")  # confirms a pausing op
            elif rs == "cancelled" or (
                rs == "partially_applied"
                and c.execute(select(operation.c.status).where(operation.c.id == t.id)).scalar() == "cancelling"
            ):  # the worker stopped as asked; what it finished stays (OUT-EXE-017)
                set_op_status(c, st.clock, t.id, "cancelled")
            else:
                set_op_status(
                    c,
                    st.clock,
                    t.id,
                    "waiting" if waiting else "succeeded" if body.ok and rs != "failed" else "failed",
                    body.result.get("waiting_reason") if waiting else None,
                )
        if handler and body.ok:
            handler(st, c, t, body.result)
        elif handler and t.job_id and t.kind == "scan":
            set_job_status(c, t.job_id, JobStatus.failed, error=str(body.result.get("error")))
    return {"status": "ok"}


def _on_scan(st: AppState, c: Any, t: Any, result: dict[str, Any]) -> None:
    c.execute(update(source).where(source.c.id == t.payload["source_id"]).values(status="online"))
    organize.upsert_entries(st, c, t.payload["source_id"], result.get("entries") or [])
    _index_source(c, t.payload["source_id"])
    organize.finish_scan(st, c, t)
    if t.payload.get("watch") and t.job_id is None:
        from .routes_watch import after_watch_scan

        after_watch_scan(st, c, t)


def _index_source(c: Any, src_id: str) -> None:
    """Scanned photos are searchable by date, folder and file name before any model has run (05 §2)."""
    from opagent_db.schema import asset_location, index_outbox

    from . import labels

    c.execute(
        index_outbox.insert().from_select(
            ["asset_id"],
            select(asset_location.c.asset_id).where(
                asset_location.c.source_id == src_id, asset_location.c.missing.is_(False)
            ),
        )
    )
    labels.drain_index(c)


def _on_estimate(st: AppState, c: Any, t: Any, result: dict[str, Any]) -> None:
    organize.finish_estimate(st, c, t.payload["job_id"], result)


def _on_apply(st: AppState, c: Any, t: Any, result: dict[str, Any]) -> None:
    pid = t.payload["plan_id"]
    done = 0
    for r in result["results"]:
        c.execute(
            update(output_action)
            .where(output_action.c.id == r["action_id"])
            .values(
                status=r["status"],
                error=r.get("error"),
                dst_rel=r.get("dst_rel") or output_action.c.dst_rel,
                conflict=r.get("conflict") or "none",
            )
        )
        done += r["status"] == "done"
    failed = sum(r["status"] == "failed" for r in result["results"])
    total = c.execute(
        select(func.count()).select_from(output_action).where(output_action.c.plan_id == pid)
    ).scalar_one()
    stopped = len(result["results"]) < total  # cancelled/paused between actions (OUT-EXE-017)
    if stopped and not done:
        status = "cancelled"
    elif stopped or failed:
        status = "partially_applied"
    else:
        status = "applied"  # identical copies skipped still count as applied
    c.execute(update(output_plan).where(output_plan.c.id == pid).values(status=status))
    set_job_status(
        c,
        t.job_id,
        JobStatus.completed,
        finished_at=st.clock.now(),
        counters={"total": total, "done": done, "failed": failed},
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
