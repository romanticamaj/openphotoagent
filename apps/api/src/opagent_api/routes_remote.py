"""Remote worker channel (02 §5.3; 09b §8.1/§8.3).

* ``welcome``: protocol negotiation (the CP serves major N and N-1; anything older gets ``upgrade_required`` and is
  given no work), the workspace the worker serves and its egress policy (02 §1.4). The worker enforces the policy
  itself before anything leaves the machine; the CP only states it.
* ``WSS /connect``: the worker's outbound channel. The CP pushes ``work.available`` so the worker does not poll;
  revoking the worker closes the socket at the next tick. ``GET /poll?wait=30`` is the long-poll fallback.
* ``PUT /thumbs/{hash}/{variant}``: thumbnail upload, refused when the workspace policy does not allow it.
"""

from __future__ import annotations

import asyncio
import io
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from fastapi.responses import Response
from opagent_db.schema import worker, worker_task
from pydantic import BaseModel
from sqlalchemy import or_, select, update

from .state import AppState

worker_router = APIRouter()
router = APIRouter()

PROTOCOL = "1.0"
MODES = ("full", "thumbs", "edge-thumbs", "results-only")
DEFAULT_POLICY = {"mode": "thumbs", "strip_gps": True, "hash_paths": False}  # self-hosted CP default (02 §1.4)
THUMB_LIMIT = {"full": 1024, "thumbs": 512, "edge-thumbs": 0, "results-only": 0}
VARIANTS = {"t256": 256, "t512": 512, "p1024": 1024}
WS_TICK_S = 0.2


def _st(req: Request | WebSocket) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def _major(v: str) -> int:
    try:
        return int(str(v).split(".")[0])
    except ValueError:
        return -1


def egress_policy(st: AppState, c: Any) -> dict[str, Any]:
    if not st.multi_user:
        return {"mode": "full", "strip_gps": False, "hash_paths": False}  # desktop: everything is local (08 C-05)
    return {**DEFAULT_POLICY, **(st.workspace_settings(c).get("egress_policy") or {})}


def welcome(st: AppState, c: Any, worker_protocol: str) -> dict[str, Any]:
    cp = _major(getattr(st, "protocol_version", PROTOCOL))
    w = _major(worker_protocol)
    status = "ok" if cp - 1 <= w <= cp else ("upgrade_required" if w < cp - 1 else "server_too_old")
    return {
        "type": "welcome",
        "status": status,
        "protocol": getattr(st, "protocol_version", PROTOCOL),
        "min_protocol": f"{max(cp - 1, 0)}.0",
        "workspaces": [st.ws_id],
        "egress_policy": egress_policy(st, c),
    }


def compatible(st: AppState, c: Any, worker_id: str) -> bool:
    caps = c.execute(select(worker.c.capabilities).where(worker.c.id == worker_id)).scalar() or {}
    return bool(welcome(st, c, str(caps.get("protocol_version") or PROTOCOL))["status"] == "ok")


def _wid(req: Request | WebSocket) -> str:
    wid = getattr(req.state, "opa_worker", None)
    if not wid:
        raise HTTPException(401, detail={"code": "AUTH_REQUIRED"})
    return str(wid)


@worker_router.get("/welcome")
def get_welcome(req: Request, protocol_version: str = PROTOCOL) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        return welcome(st, c, protocol_version)


@worker_router.get("/poll")
def poll(req: Request, wait: float = Query(30.0, ge=0, le=60)) -> Any:
    """Long-poll fallback when WSS is blocked (02 §5.3): a task, or 204 after ``wait`` seconds."""
    from .routes_worker import _lease_one

    st = _st(req)
    wid = _wid(req)
    with st.engine.begin() as c:
        if not compatible(st, c, wid):
            raise HTTPException(426, detail={"code": "UPGRADE_REQUIRED"})
    deadline = st.clock.monotonic() + wait
    while True:
        task = _lease_one(st, wid, None, req.query_params.get("boot_id"))
        if task is not None:
            return task
        if st.clock.monotonic() >= deadline:
            return Response(status_code=204)
        time.sleep(0.05)  # noqa: TID251 - long-poll inside a threadpool request handler


def _pending(st: AppState, wid: str) -> bool:
    with st.engine.begin() as c:
        return (
            c.execute(
                select(worker_task.c.id)
                .where(
                    worker_task.c.status == "pending",
                    or_(worker_task.c.worker_id.is_(None), worker_task.c.worker_id == wid),
                )
                .limit(1)
            ).first()
            is not None
        )


def _revoked(st: AppState, wid: str) -> bool:
    from opagent_db.schema import worker_credential

    with st.root.engine.begin() as c:
        r = c.execute(select(worker_credential.c.revoked_at).where(worker_credential.c.worker_id == wid)).first()
    return r is None or r.revoked_at is not None


@worker_router.websocket("/connect")
async def connect(ws: WebSocket) -> None:
    """hello → welcome, then ``work.available`` pushes; closed (4401) as soon as the worker is revoked."""
    from starlette.concurrency import run_in_threadpool

    st = _st(ws)
    wid = getattr(ws.state, "opa_worker", None)
    if not wid:
        await ws.close(code=4401)
        return
    await ws.accept()
    hello = await ws.receive_json()

    def _welcome() -> dict[str, Any]:
        with st.engine.begin() as c:
            return welcome(st, c, str(hello.get("protocol_version") or PROTOCOL))

    w = await run_in_threadpool(_welcome)
    await ws.send_json(w)
    if w["status"] != "ok":
        await ws.close(code=4426)
        return
    inbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def reader() -> None:
        try:
            while True:
                inbox.put_nowait(await ws.receive_json())
        except Exception:
            inbox.put_nowait(None)

    rt = asyncio.create_task(reader())
    announced = False
    try:
        while True:
            if await run_in_threadpool(_revoked, st, wid):
                await ws.close(code=4401)
                return
            has = await run_in_threadpool(_pending, st, wid)
            if has and not announced:
                await ws.send_json({"type": "work.available"})
            announced = has
            try:
                msg = await asyncio.wait_for(inbox.get(), timeout=WS_TICK_S)
            except TimeoutError:
                continue
            if msg is None:
                return
            if msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
            elif msg.get("type") == "ack":
                announced = False  # it leased; tell it again if more is waiting
    finally:
        rt.cancel()


HASH = re.compile(r"^[0-9a-f]{16,128}$")
MAX_THUMB_BYTES = 2 * 1024 * 1024


@worker_router.put("/thumbs/{content_hash}/{variant}")
async def put_thumb(content_hash: str, variant: str, req: Request) -> dict[str, Any]:
    from PIL import Image
    from starlette.concurrency import run_in_threadpool

    st = _st(req)
    _wid(req)
    if not HASH.match(content_hash) or variant not in VARIANTS:
        raise HTTPException(422, detail={"code": "VALIDATION"})

    def _limit() -> int:
        with st.engine.begin() as c:
            return THUMB_LIMIT[egress_policy(st, c)["mode"]]

    limit = await run_in_threadpool(_limit)
    if VARIANTS[variant] > limit:  # defence in depth: the worker should never have sent it
        raise HTTPException(403, detail={"code": "EGRESS_POLICY_DENIED"})
    data = await req.body()
    if len(data) > MAX_THUMB_BYTES:
        raise HTTPException(413, detail={"code": "TOO_LARGE"})
    try:
        im = Image.open(io.BytesIO(data))
        if im.format != "WEBP" or max(im.size) > VARIANTS[variant]:
            raise ValueError
    except Exception:
        raise HTTPException(422, detail={"code": "THUMB_INVALID"}) from None
    if st.thumbs_dir is None:
        raise HTTPException(409, detail={"code": "THUMBS_DISABLED"})
    d = st.thumbs_dir / content_hash[:2]
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{content_hash}.{variant}.webp").write_bytes(data)
    return {"stored": variant}


class EgressIn(BaseModel):
    mode: str
    strip_gps: bool = True
    hash_paths: bool = False


@router.put("/workspace/egress-policy")
def set_egress(body: EgressIn, req: Request) -> dict[str, Any]:
    from opagent_db.schema import workspace

    from .state import DEFAULT_WS

    st = _st(req)
    if not st.multi_user:
        raise HTTPException(409, detail={"code": "EGRESS_FIXED_ON_DESKTOP"})
    if body.mode not in MODES:
        raise HTTPException(422, detail={"code": "VALIDATION", "field": "mode"})
    with st.engine.begin() as c:
        s = st.workspace_settings(c)
        s["egress_policy"] = body.model_dump()
        c.execute(update(workspace).where(workspace.c.id == DEFAULT_WS).values(settings=s))
    return body.model_dump()


@router.get("/workspace/egress-policy")
def get_egress(req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        return egress_policy(st, c)
