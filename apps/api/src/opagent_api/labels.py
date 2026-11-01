"""Final labels, the review queue and search indexing (02 §3.2, 03 §3.3 「其他／不確定」, 05 §2; REV-*, ORG-009/010).

Precedence (CORE-RULE-010): a human correction/approval of the asset wins; a "skip the rest" in this job means
``other``; otherwise the model decision re-derived from stored stages (ADR-0001), where anything not accepted
goes to 「其他」 (REV-004).
"""

from __future__ import annotations

from typing import Any

from opagent_core.decide import Policy, VlmResult, decide, redecide
from opagent_core.ids import uuid7
from opagent_db.schema import event, index_outbox, job_item, result, review, search_doc
from sqlalchemy import and_, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert


def emit(c: Any, kind: str, payload: dict[str, Any]) -> None:
    c.execute(insert(event).values(kind=kind, payload=payload))


def model_outcome(raw: dict[str, Any] | None, category: str | None, confidence: float | None, policy: Policy) -> Any:
    stages = ((raw or {}).get("output") or {}).get("stages")
    if stages:
        return redecide(stages, policy)
    return decide(vlm=VlmResult(category or "other", confidence or 0.0), policy=policy)


def human_label(c: Any, asset_id: str, job_id: str | None) -> dict[str, Any] | None:
    r = c.execute(
        select(review)
        .where(review.c.asset_id == asset_id, review.c.status.in_(("corrected", "approved")))
        .order_by(review.c.resolved_at.desc().nulls_last(), review.c.created_at.desc())
        .limit(1)
    ).first()
    if r is not None:
        cat = (r.corrected or {}).get("category") or (r.suggested or {}).get("category")
        return {"category": cat, "source": r.source or "review"}
    if job_id is not None:
        s = c.execute(
            select(review.c.id).where(
                review.c.asset_id == asset_id, review.c.job_id == job_id, review.c.status == "skipped"
            )
        ).first()
        if s is not None:
            return {"category": "other", "source": "review_skip"}
    return None


def on_result(st: Any, c: Any, job_id: str, asset_id: str, result_id: str, out: dict[str, Any]) -> None:
    """Called for every new model result: review queue for needs_review, search index outbox."""
    if out.get("decision") == "needs_review" and human_label(c, asset_id, None) is None:
        exists = c.execute(select(review.c.id).where(review.c.asset_id == asset_id, review.c.job_id == job_id)).first()
        if exists is None:
            rid = uuid7(st.clock)
            stages = out.get("stages") or {}
            c.execute(
                insert(review).values(
                    id=rid,
                    asset_id=asset_id,
                    job_id=job_id,
                    result_id=result_id,
                    status="pending",
                    reason=out.get("reason") or "low_confidence",
                    suggested={
                        "category": out.get("category"),
                        "confidence": out.get("confidence"),
                        "candidates": stages.get("hints") or [],
                    },
                )
            )
            emit(c, "review.created", {"review_id": rid, "job_id": job_id, "asset_id": asset_id})
    c.execute(insert(index_outbox).values(asset_id=asset_id))


def _body(caption: str | None, tags: list[str] | None, category: str | None) -> str:
    return " ".join(x for x in [caption or "", " ".join(tags or []), category or ""] if x)


def drain_index(c: Any, limit: int = 10_000) -> int:
    """index_outbox → search_doc (latest model result + human label). Slice 9 adds zh tokenisation of ``body``."""
    rows = c.execute(
        select(index_outbox.c.id, index_outbox.c.asset_id)
        .where(index_outbox.c.processed_at.is_(None))
        .order_by(index_outbox.c.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for asset_id in {r.asset_id for r in rows}:
        res = c.execute(
            select(result).where(result.c.asset_id == asset_id).order_by(result.c.created_at.desc()).limit(1)
        ).first()
        caption = res.caption if res else None
        tags = list(res.tags or []) if res else []
        cat = res.category if res else None
        if res is not None:
            o = model_outcome(res.raw, res.category, res.confidence, Policy())
            cat = o.category if o.decision == "accepted" else cat
        hl = human_label(c, asset_id, None)
        if hl:
            cat = hl["category"]
        vals = {"caption": caption, "tags": tags, "category": cat, "body": _body(caption, tags, cat)}
        c.execute(
            pg_insert(search_doc)
            .values(asset_id=asset_id, **vals)
            .on_conflict_do_update(index_elements=["asset_id"], set_=vals)
        )
    if rows:
        from sqlalchemy import func

        c.execute(
            update(index_outbox).where(index_outbox.c.id.in_([r.id for r in rows])).values(processed_at=func.now())
        )
    return len(rows)


def labels_for(c: Any, job_id: str, policy: Policy | None = None) -> dict[str, dict[str, Any]]:
    pol = policy or Policy()
    rows = c.execute(
        select(job_item.c.asset_id, result.c.category, result.c.confidence, result.c.raw)
        .join(result, result.c.id == job_item.c.result_id)
        .where(job_item.c.job_id == job_id)
    ).all()
    out = {}
    for r in rows:
        o = model_outcome(r.raw, r.category, r.confidence, pol)
        hl = human_label(c, r.asset_id, job_id)
        if hl:
            out[r.asset_id] = {
                "category_key": hl["category"],
                "decision": "accepted",
                "confidence": 1.0,
                "source": hl["source"],
            }
        else:
            out[r.asset_id] = {
                "category_key": o.category if o.decision == "accepted" else "other",
                "decision": o.decision,
                "confidence": o.confidence,
                "source": o.source,
            }
    return out


def review_view(r: Any, c: Any) -> dict[str, Any]:
    final = None
    if r.status in ("corrected", "approved"):
        final = {
            "category": (r.corrected or {}).get("category") or (r.suggested or {}).get("category"),
            "source": r.source or "review",
        }
    elif r.status == "skipped":
        final = {"category": "other", "source": r.source or "review_skip"}
    return {
        "id": r.id,
        "asset_id": r.asset_id,
        "job_id": r.job_id,
        "status": r.status,
        "reason": r.reason,
        "suggested": r.suggested,
        "final": final,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def resolve(st: Any, c: Any, review_id: str, action: str, category: str | None = None, source: str = "review") -> Any:
    r = c.execute(select(review).where(review.c.id == review_id).with_for_update()).first()
    if r is None:
        return None
    status = {"accept": "approved", "correct": "corrected", "skip": "skipped"}[action]
    vals: dict[str, Any] = {"status": status, "resolved_at": st.clock.now(), "source": source}
    if action == "correct":
        vals["corrected"] = {"category": category}
    c.execute(update(review).where(review.c.id == review_id).values(**vals))
    c.execute(insert(index_outbox).values(asset_id=r.asset_id))
    emit(c, "review.resolved", {"review_id": review_id, "job_id": r.job_id, "status": status})
    return c.execute(select(review).where(review.c.id == review_id)).first()


def skip_rest(st: Any, c: Any, job_id: str) -> int:
    res = c.execute(
        update(review)
        .where(and_(review.c.job_id == job_id, review.c.status == "pending"))
        .values(status="skipped", source="review_skip", resolved_at=st.clock.now())
        .returning(review.c.asset_id)
    ).all()
    for r in res:
        c.execute(insert(index_outbox).values(asset_id=r.asset_id))
    emit(c, "review.skipped_rest", {"job_id": job_id, "n": len(res)})
    return len(res)
