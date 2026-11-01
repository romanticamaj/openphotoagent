"""Public REST API /api/v1 (docs/02 §5.1, §10.2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from opagent_core.ids import uuid7
from opagent_core.state_machine import JobStatus
from opagent_db.schema import job, output_action, output_plan, source, worker, worker_task
from pydantic import BaseModel, Field
from sqlalchemy import func, insert, select, update

from .db import set_job_status
from .state import DEFAULT_WS, AppState

router = APIRouter()
DEFAULT_INCLUDE = ["**/*.{jpg,jpeg,png,heic,heif,webp,tif,tiff,dng,cr3,nef,arw,raf}"]
DEFAULT_EXCLUDE = ["**/.opagent-trash/**", "**/.opagent-tmp-*", "**/@eaDir/**"]


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


class SourceIn(BaseModel):
    root_alias: str
    sub_path: str = ""
    include: list[str] | None = None
    exclude: list[str] | None = None


class Target(BaseModel):
    root_alias: str
    sub_path: str = ""


class JobIn(BaseModel):
    kind: str = "organize"
    source_id: str
    preset: str = "by-date"
    target: Target
    mode: str = "dry_run"


class PreflightIn(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    return {"deployment": "all-in-one", "single_user": True, "features": {"organize.by_date": True}}


@router.get("/roots")
def roots(req: Request) -> list[dict[str, Any]]:
    with _st(req).engine.begin() as c:
        rows = c.execute(select(worker.c.id, worker.c.roots)).all()
    return [{"worker_id": w, **r} for w, rs in rows for r in rs]


def worker_rpc(st: AppState, worker_id: str, kind: str, payload: dict[str, Any], timeout_s: float = 5.0) -> Any:
    """Synchronous request to a worker through the task queue (docs/02 §5.1 fs/browse is relayed to the worker)."""
    import time

    tid = uuid7(st.clock)
    with st.engine.begin() as c:
        c.execute(insert(worker_task).values(id=tid, kind=kind, worker_id=worker_id, payload=payload))
    deadline = st.clock.monotonic() + timeout_s
    while st.clock.monotonic() < deadline:
        with st.engine.begin() as c:
            r = c.execute(select(worker_task.c.status, worker_task.c.result).where(worker_task.c.id == tid)).first()
        if r is not None and r.status in ("done", "failed"):
            if r.status == "failed":
                raise HTTPException(409, detail={"code": "WORKER_RPC_FAILED", **(r.result or {})})
            return r.result
        time.sleep(0.03)  # noqa: TID251 - polling inside a threadpool handler
    with st.engine.begin() as c:
        c.execute(
            update(worker_task)
            .where(worker_task.c.id == tid, worker_task.c.status == "pending")
            .values(status="cancelled")
        )
    raise HTTPException(504, detail={"code": "WORKER_OFFLINE"})


@router.get("/fs/browse")
def browse(root: str, req: Request, path: str = "") -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        w = c.execute(select(worker.c.id, worker.c.roots)).all()
    owner = next((wid for wid, rs in w if any(r["alias"] == root for r in rs)), None)
    if owner is None:
        raise HTTPException(409, detail={"code": "FS_ROOT_NOT_ALLOWED"})
    res = worker_rpc(st, owner, "browse", {"root_alias": root, "path": path})
    return {"root": root, "path": path, "dirs": res["dirs"]}


@router.post("/sources", status_code=201)
def create_source(body: SourceIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        w = c.execute(select(worker.c.id, worker.c.roots)).all()
        owner = next((wid for wid, rs in w if any(r["alias"] == body.root_alias for r in rs)), None)
        if owner is None:
            raise HTTPException(409, detail={"code": "FS_ROOT_NOT_ALLOWED", "root_alias": body.root_alias})
        existing = c.execute(
            select(source.c.id).where(
                source.c.root_alias == body.root_alias, source.c.sub_path == body.sub_path.strip("/")
            )
        ).scalar()
        if existing is not None:  # one source per folder: rescans update locations in place (SCAN-005)
            return {"id": existing, "root_alias": body.root_alias, "sub_path": body.sub_path.strip("/")}
        sid = uuid7(st.clock)
        c.execute(
            insert(source).values(
                id=sid,
                workspace_id=DEFAULT_WS,
                worker_id=owner,
                root_alias=body.root_alias,
                sub_path=body.sub_path.strip("/"),
                include=body.include or DEFAULT_INCLUDE,
                exclude=body.exclude or DEFAULT_EXCLUDE,
            )
        )
    return {"id": sid, "root_alias": body.root_alias, "sub_path": body.sub_path.strip("/")}


@router.get("/sources")
def list_sources(req: Request) -> list[dict[str, Any]]:
    with _st(req).engine.begin() as c:
        return [dict(r._mapping) for r in c.execute(select(source.c.id, source.c.root_alias, source.c.sub_path))]


@router.post("/preflight")
def preflight(body: PreflightIn, req: Request) -> dict[str, Any]:
    # v0.1 skeleton: by-date needs no model; full gate registry arrives with slice 8 (06 §4)
    blockers: list[dict[str, Any]] = []
    if body.action == "job.start" and body.params.get("preset", "by-date") != "by-date":
        blockers.append({"code": "MODEL_NOT_INSTALLED"})
    return {"ready": not blockers, "blockers": blockers, "warnings": []}


@router.post("/jobs", status_code=201)
def create_job(body: JobIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        s = c.execute(select(source).where(source.c.id == body.source_id)).first()
        if s is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        jid = uuid7(st.clock)
        c.execute(
            insert(job).values(
                id=jid,
                workspace_id=DEFAULT_WS,
                kind=body.kind,
                status=JobStatus.queued.value,
                mode=body.mode,
                params=body.model_dump(),
            )
        )
        c.execute(
            insert(worker_task).values(
                id=uuid7(st.clock),
                kind="scan",
                job_id=jid,
                worker_id=s.worker_id,
                payload={
                    "job_id": jid,
                    "source_id": s.id,
                    "root_alias": s.root_alias,
                    "sub_path": s.sub_path,
                    "include": s.include, "exclude": s.exclude,
                     # never index the output folder when it lives inside the source (SCAN-024)
                     "exclude_subtrees": [body.target.sub_path.strip("/")]
                     if body.target.root_alias == s.root_alias and body.target.sub_path.strip("/") else []},
            )
        )
    return get_job(jid, req)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, req: Request) -> dict[str, Any]:
    from . import queue as q

    with _st(req).engine.begin() as c:
        r = c.execute(select(job).where(job.c.id == job_id)).first()
        cnt = q.counters(c, job_id)
    if r is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    d = dict(r._mapping)
    if cnt["total"]:
        d["counters"] = {**(d.get("counters") or {}), **cnt}
        to_infer = cnt["total"] - cnt["cached"]
        d["phases"] = {"cache_lookup": "done", "infer": "skipped" if to_infer == 0 else "running"}
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in d.items()}


@router.get("/output-plans/{plan_id}")
def get_plan(plan_id: str, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        p = c.execute(select(output_plan).where(output_plan.c.id == plan_id)).first()
        if p is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        acts = c.execute(
            select(output_action).where(output_action.c.plan_id == plan_id).order_by(output_action.c.seq)
        ).all()
    return {
        "id": p.id,
        "job_id": p.job_id,
        "status": p.status,
        "summary": p.summary,
        "actions": [{k: v for k, v in a._mapping.items() if k != "plan_id"} for a in acts],
    }


def _plan_job(c: Any, plan_id: str) -> tuple[Any, Any]:
    p = c.execute(select(output_plan).where(output_plan.c.id == plan_id).with_for_update()).first()
    if p is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    j = c.execute(select(job).where(job.c.id == p.job_id)).first()
    return p, j


MOVE_CONFIRM_TEXT_ABOVE = 10_000  # OUT-EXE-019 / 02 §6.2: very large move plans need a typed confirmation


class ApproveIn(BaseModel):
    move_confirmed: bool = False
    confirm_text: str | None = None


@router.post("/output-plans/{plan_id}/approve")
def approve(plan_id: str, req: Request, body: ApproveIn | None = None) -> dict[str, Any]:
    st = _st(req)
    body = body or ApproveIn()
    with st.engine.begin() as c:
        p, j = _plan_job(c, plan_id)
        if p.status != "draft":
            raise HTTPException(409, detail={"code": "PLAN_NOT_DRAFT", "status": p.status})
        moves = c.execute(
            select(func.count())
            .select_from(output_action)
            .where(output_action.c.plan_id == plan_id, output_action.c.action == "move")
        ).scalar_one()
        if moves and not body.move_confirmed:
            raise HTTPException(409, detail={"code": "MOVE_NOT_CONFIRMED", "moves": moves})
        if moves > MOVE_CONFIRM_TEXT_ABOVE and body.confirm_text != f"MOVE {moves}":
            raise HTTPException(409, detail={"code": "CONFIRM_TEXT_REQUIRED", "expected": f"MOVE {moves}"})
        set_job_status(c, j.id, JobStatus.applying)
        c.execute(
            update(output_plan).where(output_plan.c.id == plan_id).values(status="applying", approved_at=st.clock.now())
        )
        acts = c.execute(
            select(output_action).where(output_action.c.plan_id == plan_id).order_by(output_action.c.seq)
        ).all()
        wid = c.execute(select(worker_task.c.worker_id).where(worker_task.c.job_id == j.id)).scalars().first()
        c.execute(
            insert(worker_task).values(
                id=uuid7(st.clock),
                kind="apply",
                job_id=j.id,
                worker_id=wid,
                payload={
                    "plan_id": plan_id,
                    "actions": [
                        {
                            "action_id": a.id,
                            "op": a.action,
                            "src_alias": a.src_alias,
                            "src_rel": a.src_rel,
                            "dst_alias": a.dst_alias,
                            "dst_rel": a.dst_rel,
                            "src_hash": a.src_hash,
                        }
                        for a in acts
                    ],
                },
            )
        )
    return get_plan(plan_id, req)


@router.post("/output-plans/{plan_id}/undo")
def undo(plan_id: str, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        p, j = _plan_job(c, plan_id)
        if p.status not in ("applied", "partially_applied"):
            raise HTTPException(409, detail={"code": "PLAN_NOT_APPLIED", "status": p.status})
        set_job_status(c, j.id, JobStatus.undoing)
        c.execute(update(output_plan).where(output_plan.c.id == plan_id).values(status="undoing"))
        wid = c.execute(select(worker_task.c.worker_id).where(worker_task.c.job_id == j.id)).scalars().first()
        c.execute(
            insert(worker_task).values(
                id=uuid7(st.clock), kind="undo", job_id=j.id, worker_id=wid, payload={"plan_id": plan_id}
            )
        )
    return get_plan(plan_id, req)
