"""Offline street map (10 §2.5, ADR-0037): whether the region's map is here, one download Operation at a time (the
worker fetches and extracts it), and the file for the map's PMTiles reader, served in byte ranges."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from opagent_core.ids import uuid7
from opagent_db.schema import operation, worker, worker_task
from sqlalchemy import insert, select

from .operations import ensure_operation
from .state import AppState

router = APIRouter()
REGION = "taiwan"
NEED_FREE = 1_200_000_000  # bytes: the extract (about 100-300 MB) plus room to spare (preflight, 06 §4)
OPEN = ("queued", "running", "waiting", "paused", "pausing")


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def _file(st: AppState) -> Path | None:
    return st.home / "maps" / f"{REGION}.pmtiles" if st.home is not None else None


def _open_op(c: Any) -> str | None:
    return c.execute(  # type: ignore[no-any-return]
        select(operation.c.id)
        .where(operation.c.type == "map.download", operation.c.status.in_(OPEN))
        .order_by(operation.c.id.desc())
        .limit(1)
    ).scalar()


@router.get("/map/basemap")
def basemap(req: Request) -> dict[str, Any]:
    st = _st(req)
    f = _file(st)
    here = f is not None and f.is_file()
    with st.engine.begin() as c:
        op = _open_op(c)
    return {
        "region": REGION,
        "available": here,
        "bytes": f.stat().st_size if here and f is not None else 0,
        "operation_id": op,
    }


@router.post("/map/basemap/download", status_code=202)
def download(req: Request) -> dict[str, Any]:
    st = _st(req)
    f = _file(st)
    if f is None:
        raise HTTPException(409, detail={"code": "MAP_NO_DATA_DIR"})
    f.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(f.parent).free
    if free < NEED_FREE:
        need = {"req": NEED_FREE, "free": free}
        raise HTTPException(409, detail={"code": "DISK_INSUFFICIENT_MODELS", "params": need})
    with st.engine.begin() as c:
        op = _open_op(c)
        if op is not None:
            return {"operation_id": op}
        op = uuid7(st.clock)
        ensure_operation(c, st.clock, op, "map.download", {"kind": "basemap", "id": REGION})
        w = c.execute(select(worker.c.id).order_by(worker.c.last_heartbeat_at.desc().nulls_last()).limit(1)).scalar()
        c.execute(insert(worker_task).values(id=op, kind="basemap", worker_id=w, payload={"region": REGION}))
    return {"operation_id": op}


@router.get("/map/basemap.pmtiles")
def basemap_file(req: Request) -> FileResponse:
    f = _file(_st(req))
    if f is None or not f.is_file():
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return FileResponse(f, media_type="application/octet-stream")  # Range requests: 206 (Starlette)
