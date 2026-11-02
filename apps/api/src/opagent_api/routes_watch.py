"""Watch folders (05 §5; 09b §6). The worker watches; the API decides what a batch of new files becomes.

* One automation per source. Turning it on queues a catch-up scan, so files added while it was off are found
  (WATCH-004). The worker reports whether it gets native events or polls a network share (WATCH-006).
* A batch becomes **one** incremental scan per source: while a watch scan is still queued, later batches merge into
  it (WATCH-005). Large imports run at low priority.
* After the scan, new photos are labelled. With an organize rule the batch instead starts an organize job, which
  ends at a plan waiting for approval — ``move`` can never be auto-approved (WATCH-003, invariant 3).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from opagent_core import watch as W
from opagent_core.ids import uuid7
from opagent_db.schema import asset_location, automation, job, result, source, worker_task
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, update

from .approvals import ui_only
from .state import DEFAULT_WS, AppState

router = APIRouter()
worker_router = APIRouter()
DEFAULTS = {"debounce_s": 120, "stable_s": 10, "poll_interval_s": 300}
ACTIVE_JOB = ("queued", "scanning", "running", "planning")


class OrganizeRule(BaseModel):
    preset: str = "by-date"
    action: Literal["copy", "move"] = "copy"
    target: dict[str, str]
    advanced: dict[str, Any] = Field(default_factory=dict)
    categories: list[dict[str, Any]] | None = None


class WatchIn(BaseModel):
    enabled: bool
    debounce_s: int | None = Field(default=None, ge=1, le=3600)
    stable_s: int | None = Field(default=None, ge=0, le=3600)
    poll_interval_s: int | None = Field(default=None, ge=30, le=86400)
    organize: OrganizeRule | None = None
    approval_policy: Literal["always_manual", "auto_non_destructive"] | None = None


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def _row(c: Any, sid: str) -> Any:
    return c.execute(select(automation).where(automation.c.source_id == sid)).first()


def mode_reason(mode: str | None, fstype: str | None, poll_s: int) -> str:
    if mode == "poll":
        return (
            f"這個資料夾在網路磁碟（{fstype}）上，作業系統不會即時通知變更；"
            f"改為每 {poll_s} 秒做一次增量掃描。可在設定調整間隔。"
        )
    if mode == "native":
        return "新檔案會即時偵測（作業系統的檔案事件），停止寫入一段時間後才處理。"
    return "等待 Worker 開始監看…"


def _view(r: Any, catch_up: str | None = None) -> dict[str, Any]:
    trig = {**DEFAULTS, **(r.trigger if r else {})}
    state = r.state if r else {}
    steps = r.steps if r else []
    org = next((s["organize"] for s in steps if "organize" in s), None)
    return {
        "enabled": bool(r and r.enabled),
        **{k: trig[k] for k in DEFAULTS},
        "organize": org,
        "approval_policy": r.approval_policy if r else "always_manual",
        "mode": state.get("mode"),
        "fstype": state.get("fstype"),
        "mode_reason": mode_reason(state.get("mode"), state.get("fstype"), int(trig["poll_interval_s"])),
        "catch_up_task_id": catch_up,
    }


@router.get("/sources/{source_id}/watch")
def get_watch(source_id: str, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        if c.execute(select(source.c.id).where(source.c.id == source_id)).first() is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        return _view(_row(c, source_id))


@router.put("/sources/{source_id}/watch")
def put_watch(source_id: str, body: WatchIn, req: Request) -> dict[str, Any]:
    ui_only(req)  # adding automation is a person's decision in the app (never MCP)
    st = _st(req)
    org = body.organize
    if org is not None and org.action == "move":
        if not org.advanced.get("allow_move"):
            raise HTTPException(422, detail={"code": "MOVE_REQUIRES_ADVANCED"})
        if body.approval_policy == "auto_non_destructive":
            raise HTTPException(422, detail={"code": "WATCH_MOVE_NEEDS_APPROVAL"})  # 05 §5: move always needs a person
    with st.engine.begin() as c:
        s = c.execute(select(source).where(source.c.id == source_id)).first()
        if s is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        r = _row(c, source_id)
        trig = {**DEFAULTS, **(r.trigger if r else {}), "kind": "watch"}
        for k in DEFAULTS:
            v = getattr(body, k)
            if v is not None:
                trig[k] = v
        steps: list[dict[str, Any]] = [{"run_pipeline": {"only_unlabeled": True}}]
        if org is not None:
            steps.append({"organize": org.model_dump()})
        elif r is not None and "organize" not in body.model_fields_set:
            steps = r.steps
        policy = body.approval_policy or (r.approval_policy if r else "always_manual")
        was_on = bool(r and r.enabled)
        values = {
            "enabled": body.enabled,
            "trigger": trig,
            "steps": steps,
            "approval_policy": policy,
            "updated_at": st.clock.now(),
        }
        if r is None:
            c.execute(
                insert(automation).values(
                    id=uuid7(st.clock),
                    workspace_id=DEFAULT_WS,
                    source_id=source_id,
                    name=f"watch:{s.root_alias}/{s.sub_path}".rstrip("/"),
                    **values,
                )
            )
        else:
            c.execute(update(automation).where(automation.c.id == r.id).values(**values))
        catch_up = None
        if body.enabled and not was_on:  # files added while not watching (WATCH-004)
            catch_up = queue_batch(st, c, source_id, files=0, priority="normal", reason="catch_up")
        return _view(_row(c, source_id), catch_up)


def queue_batch(st: AppState, c: Any, source_id: str, files: int, priority: str, reason: str = "events") -> str:
    """One pending scan per source: merge into a queued one, else start a scan (or the organize rule's job)."""
    r = _row(c, source_id)
    org = next((x["organize"] for x in (r.steps if r else []) if "organize" in x), None)
    if org is not None:
        running = c.execute(
            select(job.c.id)
            .where(
                job.c.params["origin"].astext == "watch",
                job.c.params["source_id"].astext == source_id,
                job.c.status.in_(ACTIVE_JOB),
            )
            .order_by(job.c.id.desc())
        ).first()
        if running is not None:
            return str(running.id)
        waiting = c.execute(
            select(job.c.id, job.c.plan_id).where(
                job.c.params["origin"].astext == "watch",
                job.c.params["source_id"].astext == source_id,
                job.c.status == "awaiting_approval",
            )
        ).all()
        for w in waiting:  # a newer plan covers these files too; the old draft is never applied (03 plan rules)
            from opagent_core.state_machine import JobStatus
            from opagent_db.schema import output_plan

            from .db import set_job_status

            if w.plan_id:
                c.execute(
                    update(output_plan)
                    .where(output_plan.c.id == w.plan_id, output_plan.c.status == "draft")
                    .values(status="superseded")
                )
            set_job_status(c, w.id, JobStatus.completed, clock=st.clock, finished_at=st.clock.now())
        from .routes_public import JobIn, start_job

        body = JobIn(source_id=source_id, **org)
        info = {"files": files, "priority": priority, "reason": reason}
        return start_job(st, c, body, extra={"origin": "watch", "watch": info, "priority": priority})
    queued = c.execute(
        select(worker_task)
        .where(
            worker_task.c.kind == "scan",
            worker_task.c.status == "pending",
            worker_task.c.payload["source_id"].astext == source_id,
            worker_task.c.payload.has_key("watch"),
        )
        .with_for_update()
    ).first()
    if queued is not None:
        w = dict(queued.payload["watch"])
        w["files"] = int(w.get("files", 0)) + files
        w["priority"] = "low" if w["files"] > W.LARGE_BATCH or "low" in (priority, w.get("priority")) else "normal"
        c.execute(
            update(worker_task).where(worker_task.c.id == queued.id).values(payload={**queued.payload, "watch": w})
        )
        return str(queued.id)
    s = c.execute(select(source).where(source.c.id == source_id)).one()
    from .routes_viewer import scan_task

    t = scan_task(st, s, None, [])
    t["payload"]["watch"] = {"files": files, "priority": priority, "reason": reason}
    c.execute(insert(worker_task).values(**t))
    return str(t["id"])


def after_watch_scan(st: AppState, c: Any, t: Any) -> None:
    """A watch scan finished (no organize rule): label what is new (05 §5 run_pipeline)."""
    from .organize import start_label_job_in

    src_id = t.payload["source_id"]
    labelled = select(result.c.asset_id)
    ids = [
        a
        for (a,) in c.execute(
            select(asset_location.c.asset_id)
            .where(
                asset_location.c.source_id == src_id,
                asset_location.c.missing.is_(False),
                asset_location.c.asset_id.not_in(labelled),
            )
            .distinct()
            .order_by(asset_location.c.asset_id)
        )
    ]
    if ids:
        w = t.payload.get("watch") or {}
        start_label_job_in(
            st,
            c,
            ids,
            only_unlabeled=True,
            priority="low" if len(ids) > W.LARGE_BATCH else str(w.get("priority") or "normal"),
            origin={"origin": "watch", "source_id": src_id},
        )


# ---------------------------------------------------------------- worker side
class BatchIn(BaseModel):
    files: int = Field(ge=0)
    priority: Literal["normal", "low"] = "normal"
    sample: list[str] = Field(default_factory=list, max_length=20)


class StatusIn(BaseModel):
    mode: Literal["native", "poll"]
    fstype: str | None = None


@worker_router.get("/watches")
def worker_watches(worker_id: str, req: Request) -> list[dict[str, Any]]:
    with _st(req).engine.begin() as c:
        rows = c.execute(
            select(automation, source.c.root_alias, source.c.sub_path)
            .join(source, source.c.id == automation.c.source_id)
            .where(automation.c.enabled.is_(True), source.c.worker_id == worker_id)
        ).all()
    return [
        {
            "source_id": r.source_id,
            "root_alias": r.root_alias,
            "sub_path": r.sub_path,
            **{k: {**DEFAULTS, **r.trigger}[k] for k in DEFAULTS},
        }
        for r in rows
    ]


@worker_router.post("/watches/{source_id}/batch")
def worker_batch(source_id: str, body: BatchIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        r = _row(c, source_id)
        if r is None or not r.enabled:
            return {"ignored": True}
        tid = queue_batch(st, c, source_id, body.files, body.priority)
    return {"task_id": tid}


@worker_router.post("/watches/{source_id}/status")
def worker_status(source_id: str, body: StatusIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        r = _row(c, source_id)
        if r is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        c.execute(
            update(automation)
            .where(automation.c.id == r.id)
            .values(state={**r.state, "mode": body.mode, "fstype": body.fstype})
        )
    return {"ok": True}
