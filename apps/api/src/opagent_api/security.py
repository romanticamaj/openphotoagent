"""Desktop API protection (08 §1.3; DESK-011/013/016).

Active when the shell passes a launch token (it arrives on stdin, never in the environment or argv):
* every ``/api/v1`` and ``/worker-api`` request needs ``Authorization: Bearer <token>`` — or, for the browser opened
  from the tray, the HttpOnly session cookie obtained with a one-time handoff code;
* ``Host`` must name the loopback interface and ``Origin`` (when present) must be the app's own — this defeats DNS
  rebinding, where an attacker's hostname resolves to 127.0.0.1.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
APP_ORIGINS = {"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"}
HANDOFF_TTL = timedelta(seconds=30)
COOKIE = "opagent_session"
PROTECTED = ("/api/v1", "/worker-api")
LOGIN_PATH = "/api/v1/auth/login"
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = timedelta(minutes=1)
router = APIRouter()


def hash_password(password: str) -> str:
    from argon2 import PasswordHasher

    return PasswordHasher().hash(password)


def _host_ok(host: str) -> bool:
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return name.lower() in LOOPBACK_HOSTS


def _origin_ok(origin: str, host: str = "", lan: bool = False) -> bool:
    if origin in APP_ORIGINS:
        return True
    u = urlsplit(origin)
    if u.scheme not in ("http", "https"):
        return False
    if (u.hostname or "") in LOOPBACK_HOSTS:
        return True
    return lan and bool(host) and u.netloc.lower() == host.lower()  # LAN mode: same-origin pages only


class DesktopGuard:
    def __init__(self, app: ASGIApp, state: Any) -> None:
        self.app, self.state = app, state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not self.state.launch_token:
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        lan = self.state.lan_password_hash is not None
        host = headers.get("host", "")
        if not (lan or _host_ok(host)) or ("origin" in headers and not _origin_ok(headers["origin"], host, lan)):
            return await _deny(scope, receive, send, 403, "HOST_NOT_ALLOWED")
        origin = headers.get("origin", "")
        if origin in APP_ORIGINS:  # the window is the one cross-origin caller: http://tauri.localhost -> 127.0.0.1
            if scope["type"] == "http" and scope["method"] == "OPTIONS" and "access-control-request-method" in headers:
                return await _preflight(origin, headers, scope, receive, send)
            send = _with_cors(send, origin)
        path = scope.get("path", "")
        if lan and path == LOGIN_PATH:
            return await self.app(scope, receive, send)  # the one unauthenticated call in LAN mode
        if path.startswith(PROTECTED) and not self._authorized(headers, path):
            return await _deny(scope, receive, send, 401, "AUTH_REQUIRED")
        return await self.app(scope, receive, send)

    def _authorized(self, headers: dict[str, str], path: str) -> bool:
        auth = headers.get("authorization", "")
        if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].encode(), self.state.launch_token.encode()):
            return True
        if path.startswith("/api/v1"):  # browser sessions never reach the worker channel
            for part in headers.get("cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE and v in self.state.sessions:
                    return True
        return False


def _with_cors(send: Send, origin: str) -> Send:
    """No credentials: the window authenticates with the launch token, which a page on another origin does not have."""

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            extra = [(b"access-control-allow-origin", origin.encode()), (b"vary", b"Origin")]
            message = {**message, "headers": [*message.get("headers", []), *extra]}
        await send(message)

    return wrapped


async def _preflight(origin: str, headers: dict[str, str], scope: Scope, receive: Receive, send: Send) -> None:
    allow = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": headers.get("access-control-request-headers", ""),
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }
    await Response(status_code=204, headers=allow)(scope, receive, send)


async def _deny(scope: Scope, receive: Receive, send: Send, status: int, code: str) -> None:
    body = {"type": "about:blank", "title": code, "status": status, "code": code}
    await JSONResponse(body, status_code=status, media_type="application/problem+json")(scope, receive, send)


@router.post("/api/v1/auth/handoff")
def new_handoff(req: Request) -> dict[str, Any]:
    """Tray "open in browser": a one-time code valid for 30 s (the shell asks with its launch token)."""
    st = req.app.state.opa
    auth = req.headers.get("authorization", "")
    if not (
        st.launch_token
        and auth.startswith("Bearer ")
        and hmac.compare_digest(auth[7:].encode(), st.launch_token.encode())
    ):
        raise HTTPException(401, detail={"code": "AUTH_REQUIRED"})  # a browser session cannot mint new codes
    code = secrets.token_urlsafe(24)
    st.handoffs[code] = st.clock.now() + HANDOFF_TTL
    return {"code": code, "expires_in_s": int(HANDOFF_TTL.total_seconds())}


class LoginIn(BaseModel):
    password: str
    email: str | None = None  # the control plane (multi-user); desktop LAN mode has one password


@router.post(LOGIN_PATH)
def login(body: LoginIn, req: Request) -> JSONResponse:
    """LAN mode only (08 §1.3): password login → HttpOnly session cookie. 5 failures a minute per client → 429."""
    st = req.app.state.opa
    if st.multi_user:
        from .multiuser import password_login

        return _limited(st, req, lambda: password_login(st, body.email or "", body.password))
    if st.lan_password_hash is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return _limited(st, req, lambda: _lan_login(st, body.password))


def _lan_login(st: Any, password: str) -> JSONResponse:
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerificationError

    try:
        PasswordHasher().verify(st.lan_password_hash, password)
    except (VerificationError, InvalidHashError):
        raise HTTPException(401, detail={"code": "LOGIN_FAILED"}) from None
    sid = secrets.token_urlsafe(32)
    st.sessions.add(sid)
    r = JSONResponse({"ok": True})
    r.set_cookie(COOKIE, sid, httponly=True, samesite="strict", path="/")
    return r


def _limited(st: Any, req: Request, attempt: Any) -> JSONResponse:
    """5 failed logins a minute per client → 429, for the LAN password and control-plane accounts alike."""
    who = req.client.host if req.client else "?"
    now = st.clock.now()
    recent = [t for t in st.login_failures.get(who, []) if now - t < LOGIN_WINDOW]
    st.login_failures[who] = recent
    if len(recent) >= LOGIN_MAX_FAILURES:
        raise HTTPException(429, detail={"code": "LOGIN_RATE_LIMITED"})
    try:
        r = attempt()
    except HTTPException:
        recent.append(now)
        raise
    st.login_failures.pop(who, None)
    return r  # type: ignore[no-any-return]


@router.get("/auth/handoff")
def use_handoff(code: str, req: Request) -> RedirectResponse:
    st = req.app.state.opa
    exp = st.handoffs.pop(code, None)
    if exp is None or st.clock.now() > exp:
        raise HTTPException(401, detail={"code": "HANDOFF_INVALID"})
    sid = secrets.token_urlsafe(32)
    st.sessions.add(sid)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, sid, httponly=True, samesite="strict", path="/")
    return r
