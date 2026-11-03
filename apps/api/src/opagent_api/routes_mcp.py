"""「連接 AI 助手」 (04 §3.10; 05 §3.2, §3.5, §3.8): tokens, MCP settings and the call log, for the app's UI only.

A new token gets ``photos:read`` unless the person ticks more; ``organize:apply`` is never issued to an MCP token
(CLAUDE.md §3). The token value is shown once; only its SHA-256 is stored.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from opagent_core.ids import uuid7
from opagent_db.schema import mcp_call_log, mcp_token, workspace
from opagent_mcp.scopes import ALL_SCOPES, DEFAULT_SCOPES, Settings
from pydantic import BaseModel, Field
from sqlalchemy import func, insert, select, update

from .approvals import ui_only
from .mcp_server import hash_pat, new_pat
from .state import DEFAULT_WS, AppState

router = APIRouter()
NEVER_ISSUED = {"organize:apply"}
MEDIA_POLICIES = ("none", "thumb_256", "thumb_512", "thumb_1024")


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


class Folder(BaseModel):
    source: str
    path: str = ""


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    client: str | None = Field(default=None, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    folders: list[Folder] = Field(default_factory=list)
    media_policy: str | None = None


def _token_view(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "number": r.seq,
        "name": r.name,
        "client": r.client,
        "prefix": r.prefix,
        "scopes": list(r.scopes or []),
        "folders": list(r.folder_scopes or []),
        "media_policy": r.media_policy,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
        "revoked": r.revoked_at is not None,
    }


@router.post("/mcp/tokens", status_code=201)
def create_token(body: TokenIn, req: Request) -> dict[str, Any]:
    ui_only(req)
    st = _st(req)
    scopes = set(body.scopes)
    unknown = scopes - set(ALL_SCOPES)
    if unknown:
        raise HTTPException(422, detail={"code": "SCOPE_UNKNOWN", "scopes": sorted(unknown)})
    if scopes & NEVER_ISSUED:
        raise HTTPException(422, detail={"code": "SCOPE_NEVER_ISSUED", "scopes": sorted(scopes & NEVER_ISSUED)})
    if "photos:read" not in scopes:
        scopes.add("photos:read")
    owner = None
    if st.multi_user:  # the control plane: a member can only hand out what their role allows (MU-RBAC-001)
        from .multiuser import _perms
        from .permissions import capped_scopes

        u = req.state.opa_user
        if set(capped_scopes(scopes, _perms(req))) != scopes:
            raise HTTPException(403, detail={"code": "FORBIDDEN_ROLE"})
        owner = u["id"]
    for f in body.folders:
        p = f.path.strip("/")
        if p.startswith("/") or ".." in p.split("/") or "\\" in f.path or ":" in f.path:
            raise HTTPException(422, detail={"code": "PATH_INVALID", "path": f.path})
    if body.media_policy is not None and body.media_policy not in MEDIA_POLICIES:
        raise HTTPException(422, detail={"code": "MEDIA_POLICY_UNKNOWN"})
    pat = new_pat()
    tid = uuid7(st.clock)
    with st.engine.begin() as c:
        seq = (c.execute(select(func.max(mcp_token.c.seq))).scalar() or 0) + 1
        c.execute(
            insert(mcp_token).values(
                id=tid,
                seq=seq,
                name=body.name,
                client=body.client,
                token_hash=hash_pat(pat),
                prefix=pat[:16],
                scopes=[s for s in ALL_SCOPES if s in scopes],
                folder_scopes=[{"source": f.source, "path": f.path.strip("/")} for f in body.folders],
                media_policy=body.media_policy,
                owner_user_id=owner,
                created_at=st.clock.now(),
            )
        )
        row = c.execute(select(mcp_token).where(mcp_token.c.id == tid)).one()
    return {**_token_view(row), "token": pat}


@router.get("/mcp/tokens")
def list_tokens(req: Request) -> list[dict[str, Any]]:
    st = _st(req)
    with st.engine.begin() as c:
        rows = c.execute(select(mcp_token).order_by(mcp_token.c.seq)).all()
        out = []
        for r in rows:
            v = _token_view(r)
            recent = c.execute(
                select(func.count(), func.coalesce(func.sum(mcp_call_log.c.media_count), 0)).where(
                    mcp_call_log.c.token_id == r.id
                )
            ).one()
            v["calls"], v["images_returned"] = int(recent[0]), int(recent[1])
            out.append(v)
    return out


@router.delete("/mcp/tokens/{token_id}")
def revoke_token(token_id: str, req: Request) -> dict[str, Any]:
    ui_only(req)
    st = _st(req)
    mine = []
    if st.multi_user:
        from .multiuser import _perms

        if "mcp:manage" not in _perms(req):
            mine = [mcp_token.c.owner_user_id == req.state.opa_user["id"]]  # others' tokens look absent
    with st.engine.begin() as c:
        n = c.execute(
            update(mcp_token)
            .where(mcp_token.c.id == token_id, mcp_token.c.revoked_at.is_(None), *mine)
            .values(revoked_at=st.clock.now())
        ).rowcount
    if not n:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return {"revoked": True}


@router.get("/mcp/calls")
def list_calls(req: Request, token_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    st = _st(req)
    q = select(mcp_call_log).order_by(mcp_call_log.c.id.desc()).limit(max(1, min(limit, 500)))
    if token_id:
        q = q.where(mcp_call_log.c.token_id == token_id)
    with st.engine.begin() as c:
        rows = c.execute(q).all()
    return [
        {
            "at": r.at.isoformat() if r.at else None,
            "token_id": r.token_id,
            "client": r.client_name,
            "tool": r.tool,
            "method": r.method,
            "status": r.status,
            "error_code": r.error_code,
            "media_count": r.media_count,
            "latency_ms": r.latency_ms,
        }
        for r in rows
    ]


class SettingsIn(BaseModel):
    allow_apply_requests: bool | None = None
    faces_enabled: bool | None = None
    media_policy: str | None = None
    exif_free_text: bool | None = None
    auto_approve: dict[str, Any] | None = None


@router.get("/mcp/settings")
def get_settings(req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        s = (c.execute(select(workspace.c.settings).where(workspace.c.id == DEFAULT_WS)).scalar() or {}).get(
            "mcp"
        ) or {}
    d = Settings()
    return {k: s.get(k, getattr(d, k)) for k in Settings.__dataclass_fields__} | {"auto_approve": s.get("auto_approve")}


@router.put("/mcp/settings")
def put_settings(body: SettingsIn, req: Request) -> dict[str, Any]:
    from opagent_core.auto_approve import AutoApproveError, validate_auto_approve

    ui_only(req)
    st = _st(req)
    patch = body.model_dump(exclude_none=True)
    if "media_policy" in patch and patch["media_policy"] not in MEDIA_POLICIES:
        raise HTTPException(422, detail={"code": "MEDIA_POLICY_UNKNOWN"})
    if "auto_approve" in patch:
        try:
            patch["auto_approve"] = validate_auto_approve(patch["auto_approve"])
        except AutoApproveError as e:
            raise HTTPException(422, detail={"code": e.code, "actions": e.actions}) from None
    with st.engine.begin() as c:
        cur = c.execute(select(workspace.c.settings).where(workspace.c.id == DEFAULT_WS)).scalar() or {}
        mcp = {**(cur.get("mcp") or {}), **patch}
        c.execute(update(workspace).where(workspace.c.id == DEFAULT_WS).values(settings={**cur, "mcp": mcp}))
    return get_settings(req)


class ConnectIn(BaseModel):
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    folders: list[Folder] = Field(default_factory=list)


def _home(st: AppState) -> Any:
    import os
    from pathlib import Path

    return st.home or Path(os.environ.get("OPAGENT_HOME", Path.home() / ".opagent"))


@router.post("/mcp/clients/{client}/connect", status_code=201)
def connect_client(client: str, body: ConnectIn, req: Request) -> dict[str, Any]:
    """Wizard step 1: a token for this client, stored in the OS keyring (never shown, never in a config file)."""
    from opagent_mcp import credstore

    from . import mcp_clients

    if client not in mcp_clients.CLIENTS:
        raise HTTPException(404, detail={"code": "CLIENT_UNKNOWN"})
    names = {"claude-desktop": "Claude Desktop", "claude-code": "Claude Code", "cursor": "Cursor"}
    out = create_token(TokenIn(name=names[client], client=client, scopes=body.scopes, folders=body.folders), req)
    cred = credstore.save(out.pop("token"), _home(_st(req)))
    return {**out, "credential": {"store": cred["store"], "warning": cred["warning"]}}


@router.get("/mcp/clients/{client}/proposal")
def client_proposal(client: str) -> dict[str, Any]:
    from . import mcp_clients

    if client not in mcp_clients.CLIENTS:
        raise HTTPException(404, detail={"code": "CLIENT_UNKNOWN"})
    p = mcp_clients.proposal(client)
    p.pop("content")
    return p


@router.post("/mcp/clients/{client}/install")
def client_install(client: str, req: Request) -> dict[str, Any]:
    """Wizard step 2, after the person has seen the change: write only the client's known config file, with a
    backup of the previous one (MCP-PAT-002)."""
    from . import mcp_clients

    ui_only(req)
    if client not in mcp_clients.CLIENTS:
        raise HTTPException(404, detail={"code": "CLIENT_UNKNOWN"})
    return mcp_clients.install(client)
