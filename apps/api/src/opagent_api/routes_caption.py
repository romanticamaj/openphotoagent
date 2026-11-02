"""R2 caption text vectors and their blue-green reindex (05 §2; 09b §7.5; ADR-0014).

* ``POST /search/caption-index`` builds a new ``caption`` embedding space with the chosen text model: the worker
  embeds every caption in batches (``embed_captions``), progress is an Operation (``search.reindex``) with ETA.
* The first caption space becomes active by itself. Later ones stay ``building`` until the user switches; search
  keeps answering from the active space meanwhile (SRCH-R2-003). The switch is one transaction: the new space
  becomes active, the old one retired and its vectors deleted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from opagent_core.ids import uuid7
from opagent_db.schema import asset_embedding, embedding_space, search_doc, worker, worker_task
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, insert, select, text, update

from .approvals import ui_only
from .operations import ensure_operation, set_op_status
from .state import AppState

router = APIRouter()
BATCH = 256


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def caption_space(c: Any, status: str = "active") -> Any:
    return c.execute(
        select(embedding_space)
        .where(embedding_space.c.modality == "caption", embedding_space.c.status == status)
        .order_by(embedding_space.c.id.desc())
        .limit(1)
    ).first()


class IndexIn(BaseModel):
    model_key: str = Field(min_length=1, max_length=200)


@router.post("/search/caption-index", status_code=202)
def build_index(body: IndexIn, req: Request) -> dict[str, Any]:
    ui_only(req)
    st = _st(req)
    with st.engine.begin() as c:
        old = caption_space(c, "building")
        if old is not None:  # one build at a time: a newer request replaces an unfinished one
            c.execute(delete(asset_embedding).where(asset_embedding.c.space_id == old.id))
            c.execute(update(embedding_space).where(embedding_space.c.id == old.id).values(status="retired"))
        sid = c.execute(
            insert(embedding_space)
            .values(model_key=body.model_key, fingerprint="", dim=0, modality="caption", status="building")
            .returning(embedding_space.c.id)
        ).scalar_one()
        rows = c.execute(
            select(search_doc.c.asset_id, search_doc.c.caption)
            .where(search_doc.c.caption.is_not(None), search_doc.c.caption != "")
            .order_by(search_doc.c.asset_id)
        ).all()
        op = f"reindex:caption:{sid}"
        ensure_operation(c, st.clock, op, "search.reindex", {"kind": "caption_space", "id": str(sid)})
        set_op_status(c, st.clock, op, "running")
        c.execute(
            update(embedding_space)
            .where(embedding_space.c.id == sid)
            .values(thresholds={"total": len(rows), "done": 0, "operation_id": op})
        )
        w = c.execute(select(worker.c.id).order_by(worker.c.last_heartbeat_at.desc().nulls_last()).limit(1)).scalar()
        for i in range(0, len(rows), BATCH):
            c.execute(
                insert(worker_task).values(
                    id=uuid7(st.clock),
                    kind="embed_captions",
                    worker_id=w,
                    payload={
                        "space_id": sid,
                        "model": body.model_key,
                        "items": [{"asset_id": r.asset_id, "text": r.caption[:2000]} for r in rows[i : i + BATCH]],
                    },
                )
            )
        if not rows:
            _finish(st, c, sid)
    return {"space_id": sid, "operation_id": op, "total": len(rows)}


def _finish(st: AppState, c: Any, sid: int) -> None:
    sp = c.execute(select(embedding_space).where(embedding_space.c.id == sid)).one()
    set_op_status(c, st.clock, sp.thresholds["operation_id"], "succeeded")
    if caption_space(c, "active") is None:  # the first caption index needs no switch
        c.execute(update(embedding_space).where(embedding_space.c.id == sid).values(status="active"))


def on_captions_embedded(st: AppState, c: Any, t: Any, res: dict[str, Any]) -> None:
    sid = int(t.payload["space_id"])
    sp = c.execute(select(embedding_space).where(embedding_space.c.id == sid).with_for_update()).first()
    if sp is None or sp.status != "building":
        return  # replaced meanwhile
    vecs = res.get("vectors") or {}
    for aid, vec in vecs.items():
        c.execute(
            text(
                "INSERT INTO asset_embedding(asset_id, space_id, vec) VALUES (:a, :s, CAST(:v AS vector)) "
                "ON CONFLICT (asset_id, space_id) DO UPDATE SET vec = EXCLUDED.vec"
            ),
            {"a": aid, "s": sid, "v": str([float(x) for x in vec])},
        )
    th = dict(sp.thresholds or {})
    th["done"] = int(th.get("done", 0)) + len(t.payload["items"])
    c.execute(
        update(embedding_space)
        .where(embedding_space.c.id == sid)
        .values(thresholds=th, fingerprint=res.get("fingerprint") or sp.fingerprint, dim=int(res.get("dim") or sp.dim))
    )
    if th["done"] >= int(th.get("total", 0)):
        _finish(st, c, sid)


def _count(c: Any, sid: int) -> int:
    return int(
        c.execute(
            select(func.count()).select_from(asset_embedding).where(asset_embedding.c.space_id == sid)
        ).scalar_one()
    )


@router.get("/search/caption-index")
def status(req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        act, bld = caption_space(c, "active"), caption_space(c, "building")
        out: dict[str, Any] = {"active": None, "building": None}
        if act is not None:
            out["active"] = {"id": act.id, "model_key": act.model_key, "count": _count(c, act.id)}
        if bld is not None:
            th = bld.thresholds or {}
            total, done = int(th.get("total", 0)), int(th.get("done", 0))
            out["building"] = {
                "id": bld.id,
                "model_key": bld.model_key,
                "done": done,
                "total": total,
                "ready": done >= total,
                "operation_id": th.get("operation_id"),
            }
        return out


@router.post("/search/caption-index/switch")
def switch(req: Request) -> dict[str, Any]:
    ui_only(req)
    with _st(req).engine.begin() as c:
        bld = caption_space(c, "building")
        if bld is None:
            raise HTTPException(409, detail={"code": "REINDEX_NOT_READY", "reason": "nothing_building"})
        th = bld.thresholds or {}
        if int(th.get("done", 0)) < int(th.get("total", 0)):
            raise HTTPException(
                409, detail={"code": "REINDEX_NOT_READY", "done": th.get("done"), "total": th.get("total")}
            )
        old = caption_space(c, "active")
        if old is not None:
            c.execute(update(embedding_space).where(embedding_space.c.id == old.id).values(status="retired"))
            c.execute(delete(asset_embedding).where(asset_embedding.c.space_id == old.id))
        c.execute(update(embedding_space).where(embedding_space.c.id == bld.id).values(status="active"))
    return status(req)
