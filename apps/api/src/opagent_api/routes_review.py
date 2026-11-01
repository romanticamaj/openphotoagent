"""Review queue and label correction endpoints (REV-*, ORG-010, INF-CAS-005)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from opagent_db.schema import asset, job, result, review
from pydantic import BaseModel
from sqlalchemy import func, insert, select

from . import labels
from .organize import rebuild_draft_plan
from .state import AppState

router = APIRouter()


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


@router.get("/reviews")
def list_reviews(req: Request, job_id: str | None = None, status: str = "pending", limit: int = 200) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        q = select(review).where(review.c.status == status)
        cnt = select(func.count()).select_from(review).where(review.c.status == status)
        if job_id:
            q, cnt = q.where(review.c.job_id == job_id), cnt.where(review.c.job_id == job_id)
        rows = c.execute(q.order_by(review.c.created_at, review.c.id).limit(limit)).all()
        return {"count": c.execute(cnt).scalar_one(), "items": [labels.review_view(r, c) for r in rows]}


class ReviewAction(BaseModel):
    action: Literal["accept", "correct", "skip"]
    category: str | None = None


@router.post("/reviews/{review_id}")
def act(review_id: str, body: ReviewAction, req: Request) -> dict[str, Any]:
    st = _st(req)
    if body.action == "correct" and not body.category:
        raise HTTPException(422, detail={"code": "CATEGORY_REQUIRED"})
    with st.engine.begin() as c:
        r = labels.resolve(st, c, review_id, body.action, body.category)
        if r is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        labels.drain_index(c)
        if r.job_id:
            rebuild_draft_plan(st, c, r.job_id)
        return labels.review_view(r, c)


@router.post("/jobs/{job_id}/reviews/skip-rest")
def skip_rest(job_id: str, req: Request) -> dict[str, Any]:
    """「略過剩下的，全部放進『其他』」 (REV-003)."""
    st = _st(req)
    with st.engine.begin() as c:
        if c.execute(select(job.c.id).where(job.c.id == job_id)).first() is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        n = labels.skip_rest(st, c, job_id)
        labels.drain_index(c)
        plan_id = rebuild_draft_plan(st, c, job_id)
    return {"skipped": n, "plan_id": plan_id}


class LabelPatch(BaseModel):
    category: str
    tags: list[str] | None = None


@router.patch("/assets/{asset_id}/labels")
def patch_labels(asset_id: str, body: LabelPatch, req: Request) -> dict[str, Any]:
    """In-place correction from the viewer (ORG-010): becomes a corrected review that wins over the model."""
    from opagent_core.ids import uuid7

    st = _st(req)
    with st.engine.begin() as c:
        if c.execute(select(asset.c.id).where(asset.c.id == asset_id)).first() is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        rid = uuid7(st.clock)
        c.execute(
            insert(review).values(
                id=rid,
                asset_id=asset_id,
                status="corrected",
                reason="inline",
                source="inline",
                corrected={"category": body.category, **({"tags": body.tags} if body.tags is not None else {})},
                resolved_at=st.clock.now(),
            )
        )
        pending = c.execute(
            select(review.c.id).where(review.c.asset_id == asset_id, review.c.status == "pending")
        ).all()
        for p in pending:
            labels.resolve(st, c, p.id, "correct", body.category, source="inline")
        labels.emit(c, "asset.labels_changed", {"asset_id": asset_id, "category": body.category})
        from opagent_db.schema import index_outbox

        c.execute(insert(index_outbox).values(asset_id=asset_id))
        labels.drain_index(c)
        r = c.execute(select(review).where(review.c.id == rid)).first()
        return {"asset_id": asset_id, "review": labels.review_view(r, c)}


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, req: Request) -> dict[str, Any]:
    """Lightbox info panel: every label with its decision layer and model fingerprint (INF-CAS-005)."""
    from opagent_core.decide import Policy

    with _st(req).engine.begin() as c:
        a = c.execute(select(asset).where(asset.c.id == asset_id)).first()
        if a is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        rows = c.execute(select(result).where(result.c.asset_id == asset_id).order_by(result.c.created_at.desc())).all()
        out = []
        for r in rows:
            o = labels.model_outcome(r.raw, r.category, r.confidence, Policy())
            raw_out = (r.raw or {}).get("output") or {}
            out.append(
                {
                    "result_id": r.id,
                    "task_type": r.task_type,
                    "category": o.category or r.category,
                    "confidence": o.confidence,
                    "decision": o.decision,
                    "source": o.source,
                    "model_fingerprint": r.model_fingerprint,
                    "stage_fingerprint": raw_out.get("stage_fingerprint"),
                    "caption": r.caption,
                    "tags": r.tags,
                    "stages": raw_out.get("stages"),
                    "provenance": (r.raw or {}).get("provenance"),
                }
            )
        human = labels.human_label(c, asset_id, None)
        d = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in a._mapping.items()}
        return {**d, "labels": out, "human_label": human}
