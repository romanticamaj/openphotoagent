"""Public REST API /api/v1 (docs/02 §5.1, §10.2)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from opagent_core.ids import uuid7
from opagent_core.state_machine import JobStatus
from opagent_db.schema import job, job_item, output_action, output_plan, result, source, worker, worker_task
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
    action: Literal["copy", "move"] = "copy"  # 02 §6.2: copy by default; move is an advanced opt-in (ORG-004)
    advanced: dict[str, Any] = Field(default_factory=dict)


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
    warnings: list[dict[str, Any]] = []
    p = body.params
    if body.action == "job.start" and p.get("kind") == "label" and p.get("rerun_of"):
        from opagent_core.config_hash import config_hash

        with _st(req).engine.begin() as c:
            assets = [r[0] for r in c.execute(select(job_item.c.asset_id).where(job_item.c.job_id == p["rerun_of"]))]
            have = c.execute(
                select(func.count())
                .select_from(result)
                .where(
                    result.c.asset_id.in_(assets),
                    result.c.task_config_hash == config_hash(p.get("task_config") or {}),
                    result.c.model_fingerprint == p.get("fingerprint"),
                )
            ).scalar_one()
        if assets and have == len(assets):  # 100 % cache hit (06 §4.7 #51)
            warnings.append(
                {
                    "code": "JOB_NOTHING_TO_DO",
                    "severity": "warning",
                    "fixes": [
                        {"id": "reapply_thresholds", "kind": "auto_fix", "label_key": "fix.reapply_thresholds"},
                        {
                            "id": "force_rerun",
                            "kind": "choose_alternative",
                            "label_key": "fix.force_rerun",
                            "consequence_key": "fix.force_rerun.consequence",
                        },
                    ],
                }
            )
    elif body.action == "job.start" and p.get("kind", "organize") == "organize":
        from . import organize

        with _st(req).engine.begin() as c:
            running = organize.active_job_for_source(c, p.get("source_id", "")) if p.get("source_id") else None
            tc = organize.task_config_for(p.get("preset", "by-date"))
            has_model = True
            if tc is not None:
                caps = [r[0] or {} for r in c.execute(select(worker.c.capabilities))]
                has_model = any(tc["task_type"] in (cap.get("models") or {}) for cap in caps)
        if running:  # 06 §4.7 #52
            blockers.append(
                {
                    "code": "JOB_CONFLICT_RUNNING",
                    "severity": "blocker",
                    "params": {"job": running},
                    "fixes": [
                        {"id": "view_job", "kind": "open_route", "route": f"/jobs/{running}"},
                        {
                            "id": "queue_after",
                            "kind": "choose_alternative",
                            "consequence_key": "fix.queue_after.consequence",
                        },
                    ],
                }
            )
        if not has_model:
            blockers.append(
                {
                    "code": "MODEL_NOT_INSTALLED",
                    "severity": "blocker",
                    "fixes": [{"id": "download_model", "kind": "auto_fix"}],
                }
            )
    return {"ready": not blockers, "blockers": blockers, "warnings": warnings}


@router.post("/jobs", status_code=201)
def create_job(body: JobIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    if body.action == "move" and not body.advanced.get("allow_move"):
        raise HTTPException(
            422, detail={"code": "MOVE_REQUIRES_ADVANCED", "fixes": [{"id": "open_advanced", "type": "open_settings"}]}
        )
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
                    "include": s.include,
                    "exclude": s.exclude,
                    # never index the output folder when it lives inside the source (SCAN-024)
                    "exclude_subtrees": [body.target.sub_path.strip("/")]
                    if body.target.root_alias == s.root_alias and body.target.sub_path.strip("/")
                    else [],
                },
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


class PauseIn(BaseModel):
    mode: str = "graceful"


def _ensure_job_op(c: Any, st: AppState, job_id: str) -> None:
    from .operations import ensure_operation, set_op_status

    kind = c.execute(select(job.c.kind, job.c.status).where(job.c.id == job_id)).first()
    if kind is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    ensure_operation(c, st.clock, job_id, "job.label", {"kind": "job", "id": job_id})
    if kind.status == "running":
        set_op_status(c, st.clock, job_id, "running")


@router.post("/jobs/{job_id}/pause")
def pause_job(job_id: str, req: Request, body: PauseIn | None = None) -> dict[str, Any]:
    """graceful: stop issuing leases, let leased batches finish; immediate: also give leases back (Q-009/Q-010)."""
    from . import queue as q

    st = _st(req)
    with st.engine.begin() as c:
        _ensure_job_op(c, st, job_id)
        set_job_status(c, job_id, JobStatus.paused, clock=st.clock)
        if body and body.mode == "immediate":
            leased = [
                r[0]
                for r in c.execute(
                    select(job_item.c.id, job_item.c.lease_owner).where(
                        job_item.c.job_id == job_id, job_item.c.status.in_(("leased", "running"))
                    )
                )
            ]
            owners = {r.lease_owner for r in c.execute(select(job_item.c.lease_owner).where(job_item.c.id.in_(leased)))}
            for w in owners:
                q.release(c, w, leased)
    return get_job(job_id, req)


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        _ensure_job_op(c, st, job_id)
        set_job_status(c, job_id, JobStatus.running, clock=st.clock)
    return get_job(job_id, req)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, req: Request) -> dict[str, Any]:
    """running -> cancelling; pending items are cancelled; finished results are kept for the cache (Q-012)."""
    from . import queue as q

    st = _st(req)
    with st.engine.begin() as c:
        _ensure_job_op(c, st, job_id)
        cur = c.execute(select(job.c.status).where(job.c.id == job_id)).scalar_one()
        if cur in ("queued", "paused"):
            set_job_status(c, job_id, JobStatus.cancelled, clock=st.clock)
        else:
            set_job_status(c, job_id, JobStatus.cancelling, clock=st.clock)
        c.execute(
            update(job_item)
            .where(job_item.c.job_id == job_id, job_item.c.status == "pending")
            .values(status="cancelled")
        )
        q.finish_cancel_if_idle(c, st.clock, job_id)
    return get_job(job_id, req)


class ReapplyIn(BaseModel):
    thresholds: dict[str, float]


@router.post("/jobs/{job_id}/reapply")
def reapply(job_id: str, body: ReapplyIn, req: Request) -> dict[str, Any]:
    """Only thresholds changed: re-run postprocess over stored results; no job items, no inference (Q-015)."""
    from collections import Counter

    from opagent_core.decide import Policy, redecide

    st = _st(req)
    pol = Policy(accept=body.thresholds.get("accept", 0.80), review=body.thresholds.get("review", 0.50))
    with st.engine.begin() as c:
        rows = c.execute(
            select(result.c.category, result.c.confidence, result.c.raw)
            .join(job_item, job_item.c.result_id == result.c.id)
            .where(job_item.c.job_id == job_id)
        ).all()
    counts: Counter[str] = Counter()
    for r in rows:  # pure recompute from stored stages (CORE-THR-017, ADR-0001)
        out = (r.raw or {}).get("output") or {}
        stages = (
            {"emb": out.get("scores"), "n_examples": out.get("n_examples", 1)}
            if out.get("stage") == "embedding"
            else {"vlm": {"category": r.category, "p": r.confidence or 0.0}}
        )
        counts[redecide(stages, pol).decision] += 1
    return {"job_id": job_id, "decisions": dict(counts)}


class PreviewIn(BaseModel):
    source_id: str
    preset: str = "by-content-12"
    target: Target | None = None
    k: int = 10
    seed: int = 42
    kind: str = "preview"


@router.post("/previews", status_code=201)
def create_preview(body: PreviewIn, req: Request) -> dict[str, Any]:
    """10-photo preview (stratified sample) on the interactive lane (Q-018/Q-023)."""
    st = _st(req)
    with st.engine.begin() as c:
        s = c.execute(select(source).where(source.c.id == body.source_id)).first()
        if s is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        jid = uuid7(st.clock)
        params = body.model_dump()
        params["target"] = body.target.model_dump() if body.target else {"root_alias": s.root_alias, "sub_path": ""}
        c.execute(
            insert(job).values(
                id=jid, workspace_id=DEFAULT_WS, kind="preview", status=JobStatus.queued.value, params=params
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
                    "include": s.include,
                    "exclude": s.exclude,
                },
            )
        )
    return {"id": jid, "status": "running"}


class RerenderIn(BaseModel):
    thresholds: dict[str, float] | None = None
    path_template: str | None = None


def _preview_items(c: Any, j: Any, thresholds: dict[str, float] | None, template: str | None) -> list[dict[str, Any]]:
    from opagent_core.decide import Policy
    from opagent_core.planner import plan

    from . import organize

    pol = Policy(**{k: v for k, v in (thresholds or {}).items() if k in ("accept", "review")})
    labels = organize.labels_for(c, j.id, pol)
    rows = [r for r in organize._source_assets(c, j.params["source_id"]) if r.asset_id in labels]
    planned = plan(organize.plan_items(c, j, rows, labels), j.params["preset"], "", template=template)
    by = {a.asset_id: a for a in planned}
    return [
        {"asset_id": r.asset_id, "rel": r.rel_path, **labels[r.asset_id], "dst_rel": by[r.asset_id].dst_rel}
        for r in rows
    ]


@router.get("/previews/{preview_id}")
def get_preview(preview_id: str, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        j = c.execute(select(job).where(job.c.id == preview_id, job.c.kind == "preview")).first()
        if j is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        ready = bool(j.params.get("preview_ready"))
        items = _preview_items(c, j, None, None) if ready else []
    return {
        "id": preview_id,
        "status": "ready" if ready else "running",
        "items": items,
        "estimate": j.params.get("estimate"),  # library-wide forecast (ORG-007); null until the worker reports
    }


class TryIn(BaseModel):
    asset_id: str


@router.post("/previews/{preview_id}/try", status_code=201)
def try_one(preview_id: str, body: TryIn, req: Request) -> dict[str, Any]:
    """「試跑這張」: one photo through the cascade on the interactive lane; stages stream as SSE ``preview.stage``."""
    from opagent_db.schema import asset_location

    from . import organize

    st = _st(req)
    with st.engine.begin() as c:
        j = c.execute(select(job).where(job.c.id == preview_id, job.c.kind == "preview")).first()
        if j is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        s = c.execute(select(source).where(source.c.id == j.params["source_id"])).first()
        if s is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        loc = c.execute(
            select(asset_location.c.rel_path).where(
                asset_location.c.asset_id == body.asset_id, asset_location.c.source_id == s.id
            )
        ).first()
        if loc is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        tid = uuid7(st.clock)
        tc = j.params.get("task_config") or organize.task_config_for(j.params.get("preset", "by-content-12"))
        c.execute(
            insert(worker_task).values(
                id=tid,
                kind="try_one",
                lane="interactive",
                job_id=preview_id,
                worker_id=s.worker_id,
                payload={"root_alias": s.root_alias, "rel": loc.rel_path, "asset_id": body.asset_id, "task_config": tc},
            )
        )
    return {"id": tid, "lane": "interactive", "status": "pending"}


@router.get("/previews/{preview_id}/try/{task_id}")
def get_try(preview_id: str, task_id: str, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        t = c.execute(
            select(worker_task).where(worker_task.c.id == task_id, worker_task.c.job_id == preview_id)
        ).first()
    if t is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return {"id": t.id, "status": t.status, "result": t.result}


@router.post("/previews/{preview_id}/rerender")
def rerender_preview(preview_id: str, body: RerenderIn, req: Request) -> dict[str, Any]:
    """Changing thresholds or the path template re-renders from stored results: no inference (Q-024)."""
    with _st(req).engine.begin() as c:
        j = c.execute(select(job).where(job.c.id == preview_id, job.c.kind == "preview")).first()
        if j is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        if body.path_template:
            from opagent_core.path_template import TemplateError, compile_template

            try:
                compile_template(body.path_template)
            except TemplateError as e:
                raise HTTPException(422, detail={"code": e.code, "pos": e.pos}) from None
        return {"id": preview_id, "items": _preview_items(c, j, body.thresholds, body.path_template)}


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
