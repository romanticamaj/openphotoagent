"""Environment Doctor API (06 §3.5–3.6): cached results, check registry, signed auto-fix tokens with audit."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from opagent_core.ids import uuid7
from opagent_db.schema import audit_log, worker, worker_task
from sqlalchemy import insert, select

from .labels import emit
from .state import AppState

router = APIRouter()
TOKEN_TTL_S = 300
# 06 §3.6: the only actions the system may perform on the user's behalf; anything needing elevation is a copy_command
AUTO_FIX_WHITELIST = {
    "switch_port",
    "open_tcc_settings",
    "cleanup_downloads",
    "reindex",
    "restart_runtime",
    "reinstall_core_dep",
    "install_pack",
    "request_permission",
    "update",
    "regenerate_token",
}
_SECRET = secrets.token_bytes(32)


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def _sign(msg: str) -> str:
    return hmac.new(_SECRET, msg.encode(), hashlib.sha256).hexdigest()[:24]


def issue_token(worker_id: str, check_id: str, fix_id: str, now_s: int) -> str:
    msg = f"{worker_id}~{check_id}~{fix_id}~{now_s + TOKEN_TTL_S}"
    return f"{msg}~{_sign(msg)}"


def _results(c: Any, st: AppState, worker_id: str) -> list[dict[str, Any]]:
    caps = c.execute(select(worker.c.capabilities).where(worker.c.id == worker_id)).scalar()
    if caps is None:
        raise HTTPException(404, detail={"code": "WORKER_NOT_FOUND"})
    now_s = int(st.clock.now().timestamp())
    out = []
    for r in caps.get("doctor_results") or []:
        r = dict(r)
        r["fixes"] = [
            {**f, "token": issue_token(worker_id, r["check_id"], f["id"], now_s)} if f.get("type") == "auto_fix" else f
            for f in r.get("fixes") or []
        ]
        out.append(r)
    return out


@router.get("/system/doctor")
def get_doctor(req: Request, worker_id: str) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        rs = _results(c, st, worker_id)
    exp = min((r["expires_at"] for r in rs if r.get("expires_at")), default=None)
    return {"worker_id": worker_id, "results": rs, "expires_at": exp}


@router.get("/system/doctor/checks")
def check_registry() -> list[dict[str, Any]]:
    from opagent_doctor.checks import CHECKS

    return [
        {"id": c.id, "severity": c.severity, "affects": c.affects, "scope": c.scope, "group": c.group, "ttl_s": c.ttl_s}
        for c in CHECKS.values()
    ]


@router.get("/system/features")
def feature_readiness(req: Request, worker_id: str) -> dict[str, Any]:
    from opagent_doctor.checks import CheckResult, features

    st = _st(req)
    with st.engine.begin() as c:
        rs = _results(c, st, worker_id)
    objs = [CheckResult(**{k: v for k, v in r.items() if k in CheckResult.__dataclass_fields__}) for r in rs]
    return features(objs)


@router.post("/system/fixes/{rest:path}")
async def apply_fix(rest: str, req: Request) -> dict[str, Any]:
    """Execute an ``auto_fix``: only server-issued, unexpired tokens for whitelisted fixes; no client payload."""
    st = _st(req)
    if not rest.endswith("/apply"):
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    token = rest[: -len("/apply")]
    raw = await req.body()
    if raw.strip() not in (b"", b"{}"):
        raise HTTPException(400, detail={"code": "FIX_PAYLOAD_NOT_ALLOWED"})
    parts = token.split("~")
    if len(parts) != 5 or not hmac.compare_digest(_sign("~".join(parts[:4])), parts[4]):
        raise HTTPException(400, detail={"code": "FIX_TOKEN_INVALID"})
    worker_id, check_id, fix_id, exp = parts[0], parts[1], parts[2], int(parts[3])
    if int(st.clock.now().timestamp()) > exp:
        raise HTTPException(400, detail={"code": "FIX_TOKEN_EXPIRED"})
    if fix_id not in AUTO_FIX_WHITELIST:
        raise HTTPException(400, detail={"code": "FIX_NOT_ALLOWED"})
    tid = uuid7(st.clock)
    with st.engine.begin() as c:
        c.execute(
            insert(worker_task).values(
                id=tid, kind="fix", worker_id=worker_id, payload={"check_id": check_id, "fix_id": fix_id}
            )
        )
        c.execute(
            insert(audit_log).values(
                actor="local-user",
                action="fix.apply",
                target=f"{check_id}:{fix_id}",
                outcome="queued",
                details={"task": tid},
            )
        )
        emit(c, "fix.queued", {"check_id": check_id, "fix_id": fix_id, "task": tid})
    return {"status": "queued", "operation_id": tid}
