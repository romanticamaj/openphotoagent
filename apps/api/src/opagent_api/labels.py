"""Final labels, the review queue and search indexing (02 §3.2, 03 §3.3 「其他／不確定」, 05 §2; REV-*, ORG-009/010).

Precedence (CORE-RULE-010): a human correction/approval of the asset wins; a "skip the rest" in this job means
``other``; otherwise the model decision re-derived from stored stages (ADR-0001), where anything not accepted
goes to 「其他」 (REV-004).
"""

from __future__ import annotations

from typing import Any

from opagent_core.decide import Policy, VlmResult, decide, redecide
from opagent_core.ids import uuid7
from opagent_db.schema import asset, event, index_outbox, job_item, result, review, search_doc
from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert


def emit(c: Any, kind: str, payload: dict[str, Any]) -> None:
    c.execute(insert(event).values(kind=kind, payload=payload))


def model_outcome(raw: dict[str, Any] | None, category: str | None, confidence: float | None, policy: Policy) -> Any:
    stages = ((raw or {}).get("output") or {}).get("stages")
    if stages:
        return redecide(stages, policy)
    return decide(vlm=VlmResult(category or "other", confidence or 0.0), policy=policy)


def human_label(c: Any, asset_id: str, job_id: str | None) -> dict[str, Any] | None:
    for r in c.execute(  # the latest person-given category; tag-only corrections don't count
        select(review)
        .where(review.c.asset_id == asset_id, review.c.status.in_(("corrected", "approved")))
        .order_by(review.c.resolved_at.desc().nulls_last(), review.c.created_at.desc())
    ):
        cat = (r.corrected or {}).get("category") or (r.suggested or {}).get("category")
        if cat:
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
    c.execute(  # a question about a replaced result has no answer worth asking for (IMP-024)
        update(review)
        .where(review.c.asset_id == asset_id, review.c.status == "pending", review.c.job_id != job_id)
        .values(status="superseded", resolved_at=st.clock.now())
    )
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


_TOK: Any = None


def tokenizer() -> Any:
    """One tokenizer per process: vocabulary aliases + built-in category names as custom words (05 §1.3)."""
    global _TOK
    if _TOK is None:
        from opagent_search.tokenize import Tokenizer

        from .organize import preset

        words = [c.get("zh-TW", "") for c in preset("by-content-12").categories] + [
            c.get("en", "") for c in preset("by-content-12").categories
        ]
        _TOK = Tokenizer(custom_words=[w for w in words if w])
    return _TOK


def _scope_keys(c: Any, asset_id: str) -> list[str]:
    from opagent_db.schema import asset_location

    keys: set[str] = set()
    for src, rel in c.execute(
        select(asset_location.c.source_id, asset_location.c.rel_path).where(
            asset_location.c.asset_id == asset_id, asset_location.c.missing.is_(False)
        )
    ):
        parts = rel.split("/")[:-1]
        keys.add(f"src:{src}:/")
        for i in range(1, len(parts) + 1):
            keys.add(f"src:{src}:/" + "/".join(parts[:i]))
    return sorted(keys)


def _tag_key(tok: Any, t: str) -> str:
    """Canonical key for a tag label (alias → ``animal.cat``); unknown tags keep their normalised form."""
    if "." in t and t.isascii():
        return t
    k = tok.tag_key(t)
    if k:
        return str(k)
    from opagent_search.tokenize import normalize

    return normalize(t).strip()


def _person_fields(c: Any, asset_id: str) -> dict[str, Any]:
    from .faces import person_fields

    return person_fields(c, asset_id)


def index_asset(c: Any, asset_id: str) -> None:
    """Rebuild one search_doc row: latest model result + human label, facets, scope keys, weighted tsvector."""
    from opagent_db.schema import asset, asset_location
    from sqlalchemy import text as sqltext

    a = c.execute(select(asset).where(asset.c.id == asset_id)).first()
    if a is None:
        return
    res = c.execute(
        select(result).where(result.c.asset_id == asset_id).order_by(result.c.created_at.desc()).limit(1)
    ).first()
    caption = res.caption if res else None
    tags = [str(t) for t in (res.tags or [])] if res else []
    cat, conf = (res.category, res.confidence) if res else (None, None)
    review_state = None
    if res is not None:
        o = model_outcome(res.raw, res.category, res.confidence, Policy())
        cat = o.category if o.decision == "accepted" else ("other" if o.decision == "other" else cat)
        review_state = o.decision
    hr = c.execute(
        select(review)
        .where(review.c.asset_id == asset_id, review.c.status.in_(("corrected", "approved")))
        .order_by(review.c.resolved_at.desc().nulls_last(), review.c.created_at.desc())
        .limit(1)
    ).first()
    if hr is not None:
        corr = hr.corrected or {}
        cat = corr.get("category") or (hr.suggested or {}).get("category") or cat
        tags = list(corr.get("tags") or tags)
        review_state = hr.status
    tok = tokenizer()
    keys = sorted({_tag_key(tok, t) for t in tags} - {""})
    rel = c.execute(select(asset_location.c.rel_path).where(asset_location.c.asset_id == asset_id).limit(1)).scalar()
    doc = tok.document(caption=caption, tags=tags, category=cat, path=rel)
    en = " ".join(w for w in (caption or "").lower().split() if w.isascii())
    taken = a.taken_at
    vals = {
        "workspace_id": a.workspace_id,
        "caption": caption,
        "tags": keys,
        "tag_labels": tags,
        "category": cat,
        "taken_at": taken,
        "taken_year": taken.year if taken else None,
        "date_source": a.date_source,
        "scope_keys": _scope_keys(c, asset_id),
        **_person_fields(c, asset_id),
        "review_state": review_state,
        "confidence": conf,
        "body": " ".join(doc["A"] + doc["B"] + doc["C"] + doc["D"]),
    }
    c.execute(
        pg_insert(search_doc)
        .values(asset_id=asset_id, **vals)
        .on_conflict_do_update(index_elements=["asset_id"], set_={**vals, "updated_at": func.now()})
    )
    c.execute(
        sqltext(
            "UPDATE search_doc SET tsv = setweight(array_to_tsvector(CAST(:a AS text[])), 'A') "
            "|| setweight(array_to_tsvector(CAST(:b AS text[])), 'B') || setweight(to_tsvector('english', :en), 'B') "
            "|| setweight(array_to_tsvector(CAST(:c AS text[])), 'C') "
            "|| setweight(array_to_tsvector(CAST(:d AS text[])), 'D') "
            "WHERE asset_id = :id"
        ),
        {"a": doc["A"], "b": doc["B"], "en": en, "c": doc["C"], "d": doc["D"], "id": asset_id},
    )


def drain_index(c: Any, limit: int = 10_000) -> int:
    """index_outbox → search_doc, in order (02 §10, ORG-009, SRCH-SEM-006)."""
    rows = c.execute(
        select(index_outbox.c.id, index_outbox.c.asset_id)
        .where(index_outbox.c.processed_at.is_(None))
        .order_by(index_outbox.c.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for asset_id in dict.fromkeys(r.asset_id for r in rows):
        index_asset(c, asset_id)
    if rows:
        c.execute(
            update(index_outbox).where(index_outbox.c.id.in_([r.id for r in rows])).values(processed_at=func.now())
        )
    return len(rows)


def workspace_policy(c: Any) -> Policy:
    """Thresholds the user applied (e.g. from a benchmark, BENCH-003). Only decisions change; no re-inference."""
    from opagent_db.schema import workspace

    cur = c.execute(select(workspace.c.settings).where(workspace.c.id == "default")).scalar() or {}
    th = (cur.get("thresholds") or {}).get("default") or {}
    base = Policy()
    accept = float(th.get("accept", base.accept))
    return Policy(accept=accept, review=min(base.review, accept))


def labels_for(c: Any, job_id: str, policy: Policy | None = None) -> dict[str, dict[str, Any]]:
    pol = policy or workspace_policy(c)
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
    ch = c.execute(select(asset.c.content_hash).where(asset.c.id == r.asset_id)).scalar()
    return {
        "id": r.id,
        "asset_id": r.asset_id,
        "ch": ch,
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
