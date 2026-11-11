"""Operations API (06 §5.7), SSE /events, estimates (06 §5.9)."""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from opagent_db.schema import operation, result, worker
from pydantic import BaseModel
from sqlalchemy import func, select, update

from .events import replay
from .operations import op_dict, set_op_status, sweep_retention
from .state import AppState

router = APIRouter()
ACTIVE = ("queued", "waiting", "running", "pausing", "paused", "cancelling", "interrupted")


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


@router.get("/operations")
def list_operations(
    req: Request, status: str = "active", limit: int = 200, type: str | None = None
) -> list[dict[str, Any]]:
    q = select(operation)
    if type:
        q = q.where(operation.c.type == type)
    if status == "active":
        q = q.where(operation.c.status.in_(ACTIVE))
    elif status == "failed":
        q = q.where(operation.c.status == "failed")
    with _st(req).engine.begin() as c:
        rows = c.execute(q.order_by(operation.c.created_at.desc(), operation.c.id).limit(limit)).all()
        out = [op_dict(r) for r in rows]
        _with_job_state(c, out)
    return out


def _with_job_state(c: Any, ops: list[dict[str, Any]]) -> None:
    """A job's row carries where the job is and how it ended (ACT-016): a 依日期 preview is an organize job waiting
    for approval, and its row said 「整理 完成」. The page names it from `job_status` and `outcome`."""
    from opagent_db.schema import job

    ids = [o["subject"]["id"] for o in ops if (o.get("subject") or {}).get("kind") == "job" and o["subject"].get("id")]
    if not ids:
        return
    state = {
        r.id: (r.status, (r.params or {}).get("outcome"))
        for r in c.execute(select(job.c.id, job.c.status, job.c.params).where(job.c.id.in_(ids)))
    }
    for o in ops:
        sub = o.get("subject") or {}
        if sub.get("kind") == "job" and sub.get("id") in state:
            status, outcome = state[sub["id"]]
            o["subject"] = {**sub, "job_status": status, **({"outcome": outcome} if outcome else {})}


@router.get("/operations/{op_id}")
def get_operation(op_id: str, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        r = c.execute(select(operation).where(operation.c.id == op_id)).first()
    if r is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return op_dict(r)


@router.post("/operations/sweep")
def sweep_ops(req: Request) -> dict[str, int]:
    st = _st(req)
    with st.engine.begin() as c:
        return {"deleted": sweep_retention(c, st.clock)}


def _resume_blockers(c: Any, op: Any) -> list[dict[str, Any]]:
    """Every control runs preflight first (06 §5.7): resuming while the disk is still full is blocked (OP-011)."""
    prog = op.progress or {}
    if (op.waiting_reason or "").startswith("DISK_INSUFFICIENT") and prog.get("required_bytes"):
        free = c.execute(select(worker.c.disk_free).where(worker.c.id == prog.get("worker_id"))).scalar() or {}
        have = free.get(prog.get("target_alias"))
        if have is not None and have < prog["required_bytes"]:
            return [
                {
                    "code": op.waiting_reason,
                    "params": {"req": prog["required_bytes"], "free": have},
                    "fixes": [{"id": "manage_storage", "kind": "open_settings"}],
                }
            ]
    return []


def _leased(c: Any, op_id: str) -> bool:
    from opagent_db.schema import worker_task

    return bool(c.execute(select(worker_task.c.status).where(worker_task.c.id == op_id)).scalar() == "leased")


# heavy work the tray's "pause all / resume all" acts on (06 §5; downloads keep going)
HEAVY_TYPES = ("job.label", "scan", "thumbnail", "reindex", "face.reembed", "model.load", "benchmark.run")


@router.post("/operations/pause-all")
def pause_all(req: Request) -> dict[str, Any]:
    """System tray "pause all" (08 §6, DESK-020): every queued/running/waiting heavy operation → PAUSED_BY_USER."""
    st = _st(req)
    done: list[str] = []
    with st.engine.begin() as c:
        rows = c.execute(
            select(operation.c.id, operation.c.status)
            .where(operation.c.type.in_(HEAVY_TYPES), operation.c.status.in_(("queued", "running", "waiting")))
            .with_for_update()
        ).all()
        for oid, status in rows:
            if status == "waiting" and _leased(c, oid):
                set_op_status(c, st.clock, oid, "pausing")  # the worker confirms (ADR-0004)
            elif status in ("queued", "waiting"):  # not started: queued → waiting → paused (06 §5.3, ADR-0004)
                if status == "queued":
                    set_op_status(c, st.clock, oid, "waiting", "PAUSED_BY_USER")
                set_op_status(c, st.clock, oid, "paused", "PAUSED_BY_USER")
            else:
                set_op_status(c, st.clock, oid, "pausing")
                set_op_status(c, st.clock, oid, "paused", "PAUSED_BY_USER")
            done.append(oid)
    return {"paused": done}


@router.post("/operations/resume-all")
def resume_all(req: Request) -> dict[str, Any]:
    """Tray "resume all": operations the user paused (not those blocked by preflight) continue."""
    st = _st(req)
    with st.engine.begin() as c:
        ids = (
            c.execute(
                select(operation.c.id).where(
                    operation.c.type.in_(HEAVY_TYPES),
                    operation.c.status == "paused",
                    operation.c.waiting_reason == "PAUSED_BY_USER",
                )
            )
            .scalars()
            .all()
        )
    resumed: list[str] = []
    for oid in ids:
        try:
            control(oid, "resume", req)
            resumed.append(oid)
        except HTTPException:
            pass  # still blocked (e.g. preflight): stays paused with its reason shown
    return {"resumed": resumed}


@router.post("/operations/{op_id}/{action}")
def control(op_id: str, action: str, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        op = c.execute(select(operation).where(operation.c.id == op_id).with_for_update()).first()
        if op is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        if action == "resume":
            blockers = _resume_blockers(c, op)
            if blockers:
                raise HTTPException(409, detail={"code": "PREFLIGHT_BLOCKED", "blockers": blockers})
            set_op_status(c, st.clock, op_id, "running")
            if op.type == "job.label":  # an interrupted/paused job continues where it stopped (OP-006)
                from opagent_core.state_machine import JobStatus
                from opagent_db.schema import job

                from .db import set_job_status

                if c.execute(select(job.c.status).where(job.c.id == op_id)).scalar() == "paused":
                    set_job_status(c, op_id, JobStatus.running, clock=st.clock)
            from opagent_db.schema import worker_task
            from sqlalchemy import update as _upd

            c.execute(  # a paused download task resumes from its .part (06 §2.2)
                _upd(worker_task)
                .where(worker_task.c.id == op_id, worker_task.c.status == "done", worker_task.c.kind == "download")
                .values(status="pending", lease_expires_at=None)
            )
        elif action == "pause" and op.status == "waiting" and _leased(c, op_id):
            set_op_status(c, st.clock, op_id, "pausing")  # the worker confirms when it has stopped (ADR-0004)
        elif action == "pause":
            set_op_status(c, st.clock, op_id, "pausing")
            set_op_status(c, st.clock, op_id, "paused", "PAUSED_BY_USER")
        elif action == "cancel":
            held = op.status == "running" or (op.status == "waiting" and _leased(c, op_id))
            set_op_status(c, st.clock, op_id, "cancelling" if held else "cancelled")
        else:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return get_operation(op_id, req)


class ReorderIn(BaseModel):
    worker_id: str
    lane: str
    ordered_ids: list[str]


@router.post("/operations/reorder")
def reorder(body: ReorderIn, req: Request) -> dict[str, Any]:
    """Only within one worker x lane (OP-016)."""
    with _st(req).engine.begin() as c:
        rows = {r.id: r for r in c.execute(select(operation).where(operation.c.id.in_(body.ordered_ids)))}
        for oid in body.ordered_ids:
            r = rows.get(oid)
            if r is None or r.worker_id != body.worker_id or (r.progress or {}).get("lane") != body.lane:
                raise HTTPException(400, detail={"code": "REORDER_CROSS_LANE", "id": oid})
        for pos, oid in enumerate(body.ordered_ids):
            c.execute(
                update(operation)
                .where(operation.c.id == oid)
                .values(progress={**(rows[oid].progress or {}), "queue_position": pos + 1})
            )
    return {"order": body.ordered_ids}


@router.get("/events")
def events(req: Request, once: int = 0) -> Any:
    st = _st(req)
    last = req.headers.get("Last-Event-ID")
    last_id = int(last) if last and last.isdigit() else None
    if once:
        with st.engine.begin() as c:
            return PlainTextResponse(replay(c, st.clock, last_id), media_type="text/event-stream")
    from fastapi.responses import StreamingResponse

    async def gen() -> Any:
        nonlocal last_id
        import asyncio

        while not await req.is_disconnected():
            with st.engine.begin() as c:
                chunk = replay(c, st.clock, last_id)
            if chunk:
                ids = [int(ln[4:]) for ln in chunk.splitlines() if ln.startswith("id: ")]
                last_id = max(ids)
                yield chunk
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


class EstimateIn(BaseModel):
    action: str
    params: dict[str, Any]


def _bw_hint(nbytes: int) -> dict[str, str]:
    """Reference download times at 100 Mbps and 1 Gbps (06 §5.9), using the ETA rounding steps."""

    def f(seconds: float, zh: bool) -> str:
        m = seconds / 60
        if m < 1:
            return f"約 {math.ceil(seconds)} 秒" if zh else f"about {math.ceil(seconds)} s"
        v = round(m * 2) / 2 if m < 10 else round(m / 5) * 5 if m < 60 else round(m / 10) * 10
        txt = f"{v:g}"
        return f"約 {txt} 分鐘" if zh else f"about {txt} min"

    s100, s1g = nbytes * 8 / 100e6, nbytes * 8 / 1e9
    return {
        "zh-TW": f"100 Mbps {f(s100, True)} · 1 Gbps {f(s1g, True)}",
        "en": f"100 Mbps {f(s100, False)} · 1 Gbps {f(s1g, False)}",
    }


@router.post("/estimates")
def estimates(body: EstimateIn, req: Request) -> dict[str, Any]:
    """Pre-start estimate (06 §5.9, ETA-018): item counts, per-phase breakdown, download hint when the
    bandwidth is unknown (never pretend to know the user's network speed)."""
    p = body.params
    ids = p.get("asset_ids") or []
    with _st(req).engine.begin() as c:
        cached = (
            c.execute(
                select(func.count())
                .select_from(result)
                .where(
                    result.c.asset_id.in_(ids),
                    result.c.task_config_hash == p.get("task_config_hash"),
                    result.c.model_fingerprint == p.get("fingerprint"),
                )
            ).scalar_one()
            if ids
            else 0
        )
    unsupported = int(p.get("unsupported", 0))
    to_process = len(ids) - cached
    rate = float(p.get("items_per_s") or 2.5)
    breakdown = []
    if p.get("download_bytes"):
        breakdown.append(
            {
                "phase": "model_download",
                "bytes": p["download_bytes"],
                "basis": "none",
                "duration_s": None,
                "hint": _bw_hint(int(p["download_bytes"])),
            }
        )
    breakdown += [
        {"phase": "model_load", "duration_s": {"p50": float(p.get("load_s", 45))}},
        {"phase": "embed", "duration_s": {"p50": to_process / 50.0}},
        {"phase": "infer", "duration_s": {"p50": to_process / rate}, "rate_items_s": rate},
        {"phase": "postprocess", "duration_s": {"p50": to_process * 0.01}},
    ]
    p50 = sum(b["duration_s"]["p50"] for b in breakdown if b.get("duration_s"))
    return {
        "items": {
            "total": len(ids) + unsupported,
            "cached": cached,
            "to_process": to_process,
            "skipped_unsupported": unsupported,
        },
        "duration_s": {"p10": p50 * 0.8, "p50": p50, "p90": p50 * 1.3},
        "basis": p.get("basis", "registry_prior"),
        "breakdown": breakdown,
    }
