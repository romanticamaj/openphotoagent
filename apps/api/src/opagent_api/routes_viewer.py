"""Viewer endpoints (10 §2, §3.4; 02 §5.1 additions): library scan, timeline buckets, derived media.

The API serves only the derived cache (thumbnails/previews written by the Worker) and never opens originals
(invariant 1); originals stay behind the Worker relay / Tauri asset protocol.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from opagent_core.ids import uuid7
from opagent_db.schema import asset, source, worker_task
from sqlalchemy import insert, select, text

from .state import DEFAULT_WS, AppState

router = APIRouter()
UNKNOWN = "unknown"
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
IMMUTABLE = "public, max-age=31536000, immutable"


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def scan_task(st: AppState, s: Any, job_id: str | None, exclude_subtrees: list[str]) -> dict[str, Any]:
    return {
        "id": uuid7(st.clock),
        "kind": "scan",
        "job_id": job_id,
        "worker_id": s.worker_id,
        "payload": {
            "job_id": job_id,
            "source_id": s.id,
            "root_alias": s.root_alias,
            "sub_path": s.sub_path,
            "include": s.include,
            "exclude": s.exclude,
            "exclude_subtrees": exclude_subtrees,
        },
    }


@router.post("/sources/{source_id}/scan", status_code=202)
def scan_source(source_id: str, req: Request) -> dict[str, Any]:
    """Index a library folder for the viewer and search without organizing anything (onboarding step 3)."""
    st = _st(req)
    with st.engine.begin() as c:
        s = c.execute(select(source).where(source.c.id == source_id)).first()
        if s is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        from .preflight import enforce

        enforce(c, st, "scan", {"source_id": source_id})
        running = c.execute(
            select(worker_task.c.id).where(
                worker_task.c.kind == "scan",
                worker_task.c.job_id.is_(None),
                worker_task.c.payload["source_id"].astext == source_id,
                worker_task.c.status.in_(("queued", "leased")),
            )
        ).scalar()
        if running is not None:  # idempotent: one library scan per source at a time
            return {"task_id": running, "operation_id": running}
        t = scan_task(st, s, None, [])
        c.execute(insert(worker_task).values(**t))
    return {"task_id": t["id"], "operation_id": t["id"]}


@router.get("/timeline/buckets")
def buckets(req: Request) -> dict[str, Any]:
    """Per-month counts, newest first; photos with no usable date go to ``unknown`` (10 §2.3)."""
    with _st(req).engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT to_char(month_at, 'YYYY-MM') AS m, count(*) AS n FROM asset "
                "WHERE workspace_id = :ws GROUP BY month_at ORDER BY month_at DESC NULLS LAST"
            ),
            {"ws": DEFAULT_WS},
        ).all()
    return {"buckets": [{"month": r.m or UNKNOWN, "count": r.n} for r in rows]}


@router.get("/timeline/buckets/{month}")
def bucket(month: str, req: Request) -> dict[str, Any]:
    """Layout data for one month: ``{id, w, h, thumbhash, taken_at, flags}`` (10 §2.2)."""
    if month != UNKNOWN and not _MONTH.match(month):
        raise HTTPException(422, detail={"code": "VALIDATION", "field": "month"})
    q = select(
        asset.c.id, asset.c.width, asset.c.height, asset.c.thumbhash, asset.c.local_at, asset.c.date_source
    ).where(asset.c.workspace_id == DEFAULT_WS)
    if month == UNKNOWN:
        q = q.where(asset.c.month_at.is_(None)).order_by(asset.c.id)
    else:
        y, m = (int(x) for x in month.split("-"))
        q = q.where(asset.c.month_at == date(y, m, 1))
        q = q.order_by(asset.c.local_at.desc(), asset.c.id)
    with _st(req).engine.connect() as c:
        rows = c.execute(q).all()
    return {
        "month": month,
        "assets": [
            {
                "id": r.id,
                "w": r.width,
                "h": r.height,
                "thumbhash": r.thumbhash,
                "taken_at": r.local_at.isoformat() if r.local_at else None,
                "flags": ["date_from_mtime"] if r.date_source == "mtime" else [],
            }
            for r in rows
        ],
    }


@router.get("/assets/{asset_id}/media")
def media(asset_id: str, req: Request, variant: Literal["t256", "t512", "p1024"] = Query(...)) -> FileResponse:
    """Derived variants from the Worker's thumbnail cache, content-addressed and therefore immutable (10 §3.4)."""
    st = _st(req)
    with st.engine.connect() as c:
        h = c.execute(select(asset.c.content_hash).where(asset.c.id == asset_id)).scalar()
    if h is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    if st.thumbs_dir is None:
        raise HTTPException(404, detail={"code": "THUMB_MISSING"})
    p = st.thumbs_dir / h[:2] / f"{h}.{variant}.webp"
    if not p.is_file():
        raise HTTPException(404, detail={"code": "THUMB_MISSING"})
    return FileResponse(p, media_type="image/webp", headers={"Cache-Control": IMMUTABLE, "ETag": f'"{h}.{variant}"'})


@router.get("/presets/{key}")
def get_preset(key: str, lang: str = "en") -> dict[str, Any]:
    """Display names of a built-in organize preset's categories (Lightbox correction, review, wizard)."""
    from .organize import PRESETS_DIR, preset

    if not re.fullmatch(r"[a-z0-9-]+", key) or not (PRESETS_DIR / f"{key}.yaml").is_file():
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    p = preset(key)
    loc = "zh-TW" if lang.lower().startswith("zh") else "en"
    return {
        "key": key,
        "categories": [
            {
                "key": c["key"],
                "name": c.get(loc) or c.get("en") or c["key"],
                "description": (c.get("description") or {}).get(loc, ""),
            }
            for c in p.categories
        ],
    }
