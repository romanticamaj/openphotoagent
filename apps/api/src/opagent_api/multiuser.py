"""Self-hosted control plane (v0.5; 02 §1.3, §5.2, §6.3; 08 §8; ADR-0015).

Active when the app is created with ``multi_user=True`` (the Docker control plane). Then:

* people log in with e-mail + password (argon2id) or OIDC; the session cookie is HttpOnly and only its SHA-256 is
  stored; every state-changing ``/api/v1`` call also needs the ``X-CSRF-Token`` returned at login;
* remote workers pair with a one-time code (``OPA-XXXX-XXXX``, 10 minutes, single use). The worker's ed25519 private
  key never leaves it: it exchanges a refresh token plus a signature over ``worker_id|ts|nonce`` for a 15-minute
  access JWT (``aud=worker``, ``sub=worker_id``). Nonces are single-use and ``ts`` must be within ±5 minutes;
* every ``/worker-api`` message carries that JWT; a body that names another ``worker_id`` is refused
  (``WORKER_MISMATCH``) and revoking a worker kills both its access and refresh tokens on the next message.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from opagent_db.schema import (
    app_user,
    enrollment_code,
    membership,
    user_session,
    worker,
    worker_credential,
    worker_nonce,
)
from pydantic import BaseModel, Field
from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .problems import problem
from .state import DEFAULT_WS, AppState

router = APIRouter()
worker_router = APIRouter()

SESSION_TTL = timedelta(hours=12)
CODE_TTL = timedelta(minutes=10)
ACCESS_TTL = timedelta(minutes=15)
MAX_SKEW = timedelta(minutes=5)
GUESS_LIMIT, GUESS_WINDOW = 5, timedelta(minutes=10)
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
ADMIN_ROLES = {"org_owner", "org_admin"}
OPEN_PATHS = {"/api/v1/auth/login", "/api/v1/auth/oidc/start", "/api/v1/auth/oidc/callback"}
OPEN_WORKER_PATHS = {"/worker-api/v1/enroll", "/worker-api/v1/token"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
COOKIE = "opagent_session"


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def create_user(st: AppState, email: str, password: str | None, role: str = "ws_viewer", ws: str = DEFAULT_WS) -> str:
    from .security import hash_password

    uid = str(uuid.uuid4())
    with st.engine.begin() as c:
        c.execute(
            insert(app_user).values(
                id=uid, email=email.strip().lower(), password_hash=hash_password(password) if password else None
            )
        )
        c.execute(insert(membership).values(user_id=uid, workspace_id=ws, role=role))
    return uid


def open_session(st: AppState, uid: str) -> JSONResponse:
    sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with st.engine.begin() as c:
        c.execute(
            insert(user_session).values(
                id_hash=_sha(sid), user_id=uid, csrf=csrf, expires_at=st.clock.now() + SESSION_TTL
            )
        )
    r = JSONResponse({"ok": True, "csrf": csrf})
    r.set_cookie(COOKIE, sid, httponly=True, samesite="strict", path="/", secure=st.public_url.startswith("https://"))
    return r


def password_login(st: AppState, email: str, password: str) -> JSONResponse:
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerificationError

    with st.engine.begin() as c:
        u = c.execute(select(app_user).where(app_user.c.email == email.strip().lower())).first()
    try:
        if u is None or u.disabled or not u.password_hash:
            raise VerificationError
        PasswordHasher().verify(u.password_hash, password)
    except (VerificationError, InvalidHashError):
        raise HTTPException(401, detail={"code": "LOGIN_FAILED"}) from None
    return open_session(st, u.id)


def session_user(st: AppState, sid: str) -> dict[str, Any] | None:
    with st.engine.begin() as c:
        s = c.execute(select(user_session).where(user_session.c.id_hash == _sha(sid))).first()
        if s is None or _aware(s.expires_at) <= st.clock.now():
            return None
        u = c.execute(select(app_user).where(app_user.c.id == s.user_id)).first()
        if u is None or u.disabled:
            return None
        roles = {r.workspace_id: r.role for r in c.execute(select(membership).where(membership.c.user_id == u.id))}
    return {"id": u.id, "email": u.email, "roles": roles, "csrf": s.csrf}


def _aware(ts: Any) -> Any:
    from datetime import UTC

    return ts if ts is None or ts.tzinfo else ts.replace(tzinfo=UTC)


def require_role(req: Request, allowed: set[str], ws: str = DEFAULT_WS) -> dict[str, Any]:
    u = getattr(req.state, "opa_user", None)
    if u is None:
        raise HTTPException(401, detail={"code": "AUTH_REQUIRED"})
    if u["roles"].get(ws) not in allowed and not (set(u["roles"].values()) & {"org_owner"}):
        raise HTTPException(403, detail={"code": "FORBIDDEN_ROLE"})
    return u  # type: ignore[no-any-return]


# ---------------------------------------------------------------- worker pairing


def _new_code() -> str:
    pick = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"OPA-{pick[:4]}-{pick[4:]}"


class CodeIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.post("/workers/enrollment-codes", status_code=201)
def new_code(body: CodeIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    u = require_role(req, ADMIN_ROLES)
    code = _new_code()
    with st.engine.begin() as c:
        c.execute(
            insert(enrollment_code).values(
                code_hash=_sha(code), name=body.name, created_by=u["id"], expires_at=st.clock.now() + CODE_TTL
            )
        )
    return {"code": code, "expires_in_s": int(CODE_TTL.total_seconds())}


@router.delete("/workers/{worker_id}")
def revoke(worker_id: str, req: Request) -> dict[str, Any]:
    st = _st(req)
    require_role(req, ADMIN_ROLES)
    with st.engine.begin() as c:
        n = c.execute(
            update(worker_credential)
            .where(worker_credential.c.worker_id == worker_id, worker_credential.c.revoked_at.is_(None))
            .values(revoked_at=st.clock.now())
        ).rowcount
        c.execute(update(worker).where(worker.c.id == worker_id).values(status="revoked"))
    if not n:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return {"revoked": worker_id}


class EnrollIn(BaseModel):
    code: str = Field(max_length=32)
    public_key: str = Field(max_length=100)
    name: str = Field(min_length=1, max_length=100)
    protocol_version: str = "1.0"


def server_pin(st: AppState) -> str:
    return "sha256:" + hashlib.sha256(st.jwt_secret.encode() if st.jwt_secret else b"").hexdigest()[:32]


@worker_router.post("/enroll", status_code=201)
def enroll(body: EnrollIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    who = req.client.host if req.client else "?"
    now = st.clock.now()
    recent = [t for t in st.enroll_guesses.get(who, []) if now - t < GUESS_WINDOW]
    st.enroll_guesses[who] = recent
    if len(recent) >= GUESS_LIMIT:
        raise HTTPException(429, detail={"code": "ENROLL_RATE_LIMITED"})
    try:
        raw = base64.b64decode(body.public_key, validate=True)
        if len(raw) != 32:
            raise ValueError
    except ValueError:
        raise HTTPException(422, detail={"code": "VALIDATION", "field": "public_key"}) from None
    with st.engine.begin() as c:
        row = c.execute(
            select(enrollment_code)
            .where(enrollment_code.c.code_hash == _sha(body.code.strip().upper()))
            .with_for_update()
        ).first()
        if row is None:
            recent.append(now)  # only unknown codes count as guesses; a used/expired one was a real code
            raise HTTPException(401, detail={"code": "ENROLL_CODE_INVALID"})
        if row.used_at is not None or _aware(row.expires_at) <= now:
            raise HTTPException(401, detail={"code": "ENROLL_CODE_INVALID"})
        c.execute(update(enrollment_code).where(enrollment_code.c.code_hash == row.code_hash).values(used_at=now))
        wid = "w-" + uuid.uuid4().hex[:16]
        refresh = "opagent_wrt_" + secrets.token_urlsafe(32)
        c.execute(
            insert(worker_credential).values(worker_id=wid, public_key=body.public_key, refresh_hash=_sha(refresh))
        )
        c.execute(
            pg_insert(worker)
            .values(id=wid, name=body.name, roots=[], status="offline", capabilities={"deployment": "remote"})
            .on_conflict_do_nothing()
        )
    return {"worker_id": wid, "refresh_token": refresh, "server_pubkey_pin": server_pin(st)}


class TokenIn(BaseModel):
    worker_id: str = Field(max_length=64)
    ts: str = Field(max_length=64)
    nonce: str = Field(min_length=1, max_length=128)
    sig: str = Field(max_length=200)


def _deny(code: str, status: int = 401) -> HTTPException:
    return HTTPException(status, detail={"code": code})


@worker_router.post("/token")
def token(body: TokenIn, req: Request) -> dict[str, Any]:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    st = _st(req)
    auth = req.headers.get("authorization", "")
    refresh = auth[7:] if auth.startswith("Bearer ") else ""
    now = st.clock.now()
    with st.engine.begin() as c:
        cred = c.execute(select(worker_credential).where(worker_credential.c.worker_id == body.worker_id)).first()
        if cred is None or cred.revoked_at is not None or not hmac.compare_digest(cred.refresh_hash, _sha(refresh)):
            raise _deny("REFRESH_INVALID")
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(cred.public_key)).verify(
                base64.b64decode(body.sig), f"{body.worker_id}|{body.ts}|{body.nonce}".encode()
            )
        except (InvalidSignature, ValueError):
            raise _deny("SIGNATURE_INVALID") from None
        try:
            ts = _aware(datetime.fromisoformat(body.ts))
        except ValueError:
            raise _deny("CLOCK_SKEW") from None
        if abs(now - ts) > MAX_SKEW:
            raise _deny("CLOCK_SKEW")
        c.execute(delete(worker_nonce).where(worker_nonce.c.at < now - 2 * MAX_SKEW))  # older ones fail the skew check
        fresh = c.execute(
            pg_insert(worker_nonce)
            .values(worker_id=body.worker_id, nonce=body.nonce, at=now)
            .on_conflict_do_nothing()
            .returning(worker_nonce.c.nonce)
        ).first()
        if fresh is None:
            raise _deny("NONCE_REPLAYED")
    iat = int(now.timestamp())
    claims = {"sub": body.worker_id, "aud": "worker", "iat": iat, "exp": iat + int(ACCESS_TTL.total_seconds())}
    claims["jti"] = secrets.token_hex(8)
    return {
        "access_token": jwt.encode(claims, st.jwt_secret or "", algorithm="HS256"),
        "expires_in_s": int(ACCESS_TTL.total_seconds()),
    }


def verify_worker_jwt(st: AppState, tok: str) -> str | None:
    try:
        claims = jwt.decode(
            tok,
            st.jwt_secret or "",
            algorithms=["HS256"],
            audience="worker",
            options={"verify_exp": False, "require": ["sub", "aud", "exp", "iat"]},
        )
    except jwt.PyJWTError:
        return None
    if claims["exp"] <= st.clock.now().timestamp():
        return None
    sub = str(claims["sub"])
    with st.engine.begin() as c:
        rev = c.execute(select(worker_credential.c.revoked_at).where(worker_credential.c.worker_id == sub)).first()
    if rev is None or rev.revoked_at is not None:
        return None
    return sub


# ---------------------------------------------------------------- the guard


class ControlPlaneGuard:
    def __init__(self, app: ASGIApp, state: AppState) -> None:
        self.app, self.state = app, state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.state.multi_user:
            return await self.app(scope, receive, send)
        path, method = scope.get("path", ""), scope.get("method", "GET")
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        st = self.state
        if path.startswith("/worker-api"):
            if path in OPEN_WORKER_PATHS:
                return await self.app(scope, receive, send)
            auth = headers.get("authorization", "")
            sub = verify_worker_jwt(st, auth[7:]) if auth.startswith("Bearer ") else None
            if sub is None:
                return await problem(401, "AUTH_REQUIRED")(scope, receive, send)
            body, receive = await _buffer(receive)
            claimed = _claimed_worker(body)
            if claimed is not None and claimed != sub:
                return await problem(403, "WORKER_MISMATCH")(scope, receive, send)
            scope.setdefault("state", {})["opa_worker"] = sub
            return await self.app(scope, receive, send)
        if path.startswith("/api/v1") and path not in OPEN_PATHS:
            sid = _cookie(headers.get("cookie", ""))
            user = session_user(st, sid) if sid else None
            if user is None:
                return await problem(401, "AUTH_REQUIRED")(scope, receive, send)
            if method not in SAFE_METHODS and not hmac.compare_digest(
                headers.get("x-csrf-token", "").encode(), user["csrf"].encode()
            ):
                return await problem(403, "CSRF_CHECK_FAILED")(scope, receive, send)
            scope.setdefault("state", {})["opa_user"] = user
        return await self.app(scope, receive, send)


def _cookie(raw: str) -> str | None:
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and v:
            return v
    return None


def _claimed_worker(body: bytes) -> str | None:
    if not body:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return str(data["worker_id"]) if isinstance(data, dict) and "worker_id" in data else None


async def _buffer(receive: Receive) -> tuple[bytes, Receive]:
    chunks: list[bytes] = []
    more = True
    while more:
        m = await receive()
        if m["type"] != "http.request":
            break
        chunks.append(m.get("body", b""))
        more = m.get("more_body", False)
    body = b"".join(chunks)
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return body, replay
