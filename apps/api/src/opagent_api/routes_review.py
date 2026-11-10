"""Review queue and label correction endpoints (REV-*, ORG-010, INF-CAS-005)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from opagent_db.schema import asset, asset_location, job, result, review, source
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
        q, cnt = select(review), select(func.count()).select_from(review)
        if status != "all":
            q, cnt = q.where(review.c.status == status), cnt.where(review.c.status == status)
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

    st = _st(req)
    with st.engine.begin() as c:
        if c.execute(select(asset.c.id).where(asset.c.id == asset_id)).first() is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        rid = apply_correction(st, c, asset_id, body.category, body.tags, reason="inline")
        labels.drain_index(c)
        r = c.execute(select(review).where(review.c.id == rid)).first()
        return {"asset_id": asset_id, "review": labels.review_view(r, c)}


def apply_correction(
    st: Any,
    c: Any,
    asset_id: str,
    category: str | None,
    tags: list[str] | None,
    reason: str,
    caption: str | None = None,
    note: str | None = None,
) -> str:
    """A person's label for one photo: a corrected review that wins over the model and resolves pending ones."""
    from opagent_core.ids import uuid7
    from opagent_db.schema import index_outbox

    rid = uuid7(st.clock)
    corrected: dict[str, Any] = {}
    if category is not None:
        corrected["category"] = category
    if tags is not None:
        corrected["tags"] = tags
    if caption is not None:
        corrected["caption"] = caption
    if note:
        corrected["note"] = note[:500]
    c.execute(
        insert(review).values(
            id=rid,
            asset_id=asset_id,
            status="corrected",
            reason=reason,
            source=reason,
            corrected=corrected,
            resolved_at=st.clock.now(),
        )
    )
    if category is not None:
        for p in c.execute(
            select(review.c.id).where(review.c.asset_id == asset_id, review.c.status == "pending")
        ).all():
            labels.resolve(st, c, p.id, "correct", category, source=reason)
    labels.emit(c, "asset.labels_changed", {"asset_id": asset_id, "category": category})
    c.execute(insert(index_outbox).values(asset_id=asset_id))
    return rid


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, req: Request, lang: str = "zh-TW") -> dict[str, Any]:
    """Lightbox info panel: every label with its decision layer and model fingerprint (INF-CAS-005)."""
    from opagent_core.decide import Policy

    from .search import tag_names

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
                    "tag_names": tag_names([str(t) for t in r.tags or []], lang),
                    "stages": raw_out.get("stages"),
                    "provenance": (r.raw or {}).get("provenance"),
                }
            )
        human = labels.human_label(c, asset_id, None)
        locs = c.execute(
            select(asset_location.c.source_id, source.c.root_alias, asset_location.c.rel_path, asset_location.c.missing)
            .join(source, source.c.id == asset_location.c.source_id)
            .where(asset_location.c.asset_id == asset_id)
            .order_by(asset_location.c.rel_path)
        ).all()
        d = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in a._mapping.items()}
        return {
            **d,
            "labels": out,
            "human_label": human,
            "locations": [dict(r._mapping) for r in locs],
            **dict(zip(("people", "people_faces"), _people_in(c, asset_id), strict=True)),
            "faces_state": _faces_state(c, [r.source_id for r in locs]),
            "faces_found": _faces_found(c, asset_id),
        }


def _faces_found(c: Any, asset_id: str) -> dict[str, int]:
    """Faces in this photo that belong to no one shown: too small to recognise (tier 0), or of someone seen too
    rarely to form a person (VIEW-028)."""
    from opagent_db.schema import face, person

    rows = c.execute(
        select(face.c.quality_tier)
        .select_from(face.outerjoin(person, person.c.id == face.c.person_id))
        .where(face.c.asset_id == asset_id, (face.c.person_id.is_(None)) | (person.c.status == "ignored"))
    ).all()
    return {"total": len(rows), "small": sum(1 for (t,) in rows if t == 0)}


def _faces_state(c: Any, sources: list[str]) -> str:
    """off: faces are off; folder_off: on, but not for this photo's folder; on (VIEW-027)."""
    from .faces import enabled_for, fsettings

    if not fsettings(c).get("enabled"):
        return "off"
    return "on" if any(enabled_for(c, sid) for sid in sources) else "folder_off"


def _people_in(c: Any, asset_id: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Who is in the photo (VIEW-021); nothing while faces are off, never someone the person chose to ignore."""
    from opagent_db.schema import face, person

    from .faces import fsettings

    if not fsettings(c).get("enabled"):
        return [], {}
    ch = c.execute(select(asset.c.content_hash).where(asset.c.id == asset_id)).scalar()
    rows = c.execute(
        select(person.c.id, person.c.display_name, face.c.bbox)
        .join(face, face.c.person_id == person.c.id)
        .where(
            face.c.asset_id == asset_id,
            face.c.state.in_(("auto", "confirmed")),
            person.c.status != "ignored",
            person.c.merged_into.is_(None),
        )
        .distinct(person.c.id)
        .order_by(person.c.id)
    ).all()
    rows = sorted(rows, key=lambda r: (r.display_name is None, r.display_name or "", r.id))
    return [{"id": r.id, "name": r.display_name} for r in rows], {
        r.id: {"bbox": list(r.bbox), "ch": ch}
        for r in rows  # the face in this photo, for 「未命名」 (VIEW-025)
    }
