"""Selections and batch actions (10 §6): a selection is a query handle — "select all 3,214 results" sends the
query, not 3,214 IDs; the server resolves it. Batch labels become corrections (reviews); "move to folder" only
produces a draft output plan that a person approves (invariant 3/5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from opagent_core.ids import uuid7
from opagent_core.planner import PlanItem, assign_collisions, plan, summarize
from opagent_db.schema import asset, asset_location, job, output_action, output_plan, selection, source
from pydantic import BaseModel, Field
from sqlalchemy import insert, select

from . import labels
from .state import DEFAULT_WS, AppState

router = APIRouter()
MAX_SELECTION = 200_000


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


class SelectionIn(BaseModel):
    query: dict[str, Any] | None = None
    asset_ids: list[str] | None = None


def _resolve_query(st: AppState, query: dict[str, Any]) -> list[str]:
    from .search import SearchIn, run_search

    body = SearchIn(**{**query, "limit": 500, "cursor": None, "facets": []})
    ids: list[str] = []
    while True:
        r = run_search(st, body)
        ids += [i["asset_id"] for i in r["items"]]
        if len(ids) > MAX_SELECTION:  # never truncate silently: the batch must cover what was counted (ADR-0005)
            raise HTTPException(
                422,
                detail={
                    "code": "SELECTION_TOO_LARGE",
                    "params": {"n": r["total"], "max": MAX_SELECTION},
                    "fixes": [{"id": "narrow_filters", "type": "open_docs", "target": "search/filters"}],
                },
            )
        if not r["next_cursor"]:
            return ids
        body = SearchIn(**{**query, "limit": 500, "cursor": r["next_cursor"], "facets": []})


@router.post("/selections", status_code=201)
def create_selection(body: SelectionIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    if body.query is None and not body.asset_ids:
        raise HTTPException(422, detail={"code": "SELECTION_EMPTY"})
    ids = _resolve_query(st, body.query) if body.query is not None else list(dict.fromkeys(body.asset_ids or []))
    sid = uuid7(st.clock)
    with st.engine.begin() as c:
        c.execute(insert(selection).values(id=sid, query=body.query, asset_ids=ids, count=len(ids)))
    return {"id": sid, "count": len(ids), "kind": "query" if body.query is not None else "ids"}


def _ids(c: Any, sid: str) -> list[str]:
    r = c.execute(select(selection.c.asset_ids).where(selection.c.id == sid)).first()
    if r is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return list(r.asset_ids or [])


class BatchLabels(BaseModel):
    category: str | None = None
    tags: list[str] | None = None


@router.post("/selections/{sid}/labels")
def batch_labels(sid: str, body: BatchLabels, req: Request) -> dict[str, Any]:
    """Category / tags for every selected photo: corrected reviews that win over the model (ORG-010)."""
    from .routes_review import apply_correction

    st = _st(req)
    if body.category is None and body.tags is None:
        raise HTTPException(422, detail={"code": "NOTHING_TO_CHANGE"})
    with st.engine.begin() as c:
        ids = _ids(c, sid)
        for aid in ids:
            apply_correction(st, c, aid, body.category, body.tags, reason="batch")
        labels.drain_index(c)
    return {"updated": len(ids)}


class Target(BaseModel):
    root_alias: str
    sub_path: str = ""


class BatchOrganize(BaseModel):
    target: Target
    action: str = Field(default="copy", pattern="^(copy|move)$")


@router.post("/selections/{sid}/organize", status_code=201)
def batch_organize(sid: str, body: BatchOrganize, req: Request) -> dict[str, Any]:
    """「移到資料夾」: a draft plan copying (or moving) the selected photos into one folder, for approval."""
    st = _st(req)
    with st.engine.begin() as c:
        ids = _ids(c, sid)
        rows = c.execute(
            select(
                asset_location.c.asset_id,
                asset_location.c.rel_path,
                asset_location.c.mtime,
                source.c.root_alias,
                source.c.worker_id,
                asset.c.content_hash,
                asset.c.taken_at,
                asset.c.date_source,
            )
            .join(source, source.c.id == asset_location.c.source_id)
            .join(asset, asset.c.id == asset_location.c.asset_id)
            .where(asset_location.c.asset_id.in_(ids), asset_location.c.missing.is_(False))
            .order_by(asset_location.c.asset_id, asset_location.c.rel_path)
        ).all()
        first: dict[str, Any] = {}
        for r in rows:
            first.setdefault(r.asset_id, r)
        picked = [first[a] for a in ids if a in first]
        if not picked:
            raise HTTPException(422, detail={"code": "SELECTION_EMPTY"})
        items = [
            PlanItem(
                asset_id=r.asset_id,
                src_rel=r.rel_path,
                content_hash=r.content_hash,
                taken_at=r.taken_at,
                taken_at_source=r.date_source,
                mtime=r.mtime,
            )
            for r in picked
        ]
        planned = assign_collisions(plan(items, "by-date", body.target.sub_path.strip("/"), template="{stem}{ext}"))
        jid, pid = uuid7(st.clock), uuid7(st.clock)
        src_alias = {r.asset_id: r.root_alias for r in picked}
        c.execute(
            insert(job).values(
                id=jid,
                workspace_id=DEFAULT_WS,
                kind="organize",
                status="awaiting_approval",
                mode="dry_run",
                plan_id=pid,
                params={
                    "preset": "selection",
                    "selection_id": sid,
                    "action": body.action,
                    "target": body.target.model_dump(),
                    "worker_id": picked[0].worker_id,
                },
            )
        )
        c.execute(insert(output_plan).values(id=pid, job_id=jid, status="draft", summary=summarize(planned)))
        c.execute(
            insert(output_action),
            [
                {
                    "id": uuid7(st.clock),
                    "plan_id": pid,
                    "seq": i,
                    "asset_id": a.asset_id,
                    "action": body.action,
                    "src_alias": src_alias[a.asset_id],
                    "src_rel": a.src_rel,
                    "dst_alias": body.target.root_alias,
                    "dst_rel": a.dst_rel,
                    "src_hash": a.src_hash,
                    "date_source": a.date_source,
                }
                for i, a in enumerate(planned)
            ],
        )
    return {"job_id": jid, "plan_id": pid, "count": len(planned)}
