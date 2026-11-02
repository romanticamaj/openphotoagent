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
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
APP_ORIGINS = {"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"}
HANDOFF_TTL = timedelta(seconds=30)
COOKIE = "opagent_session"
PROTECTED = ("/api/v1", "/worker-api")
router = APIRouter()


def _host_ok(host: str) -> bool:
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return name.lower() in LOOPBACK_HOSTS


def _origin_ok(origin: str) -> bool:
    if origin in APP_ORIGINS:
        return True
    u = urlsplit(origin)
    return u.scheme in ("http", "https") and (u.hostname or "") in LOOPBACK_HOSTS


class DesktopGuard:
    def __init__(self, app: ASGIApp, state: Any) -> None:
        self.app, self.state = app, state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not self.state.launch_token:
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if not _host_ok(headers.get("host", "")) or ("origin" in headers and not _origin_ok(headers["origin"])):
            return await _deny(scope, receive, send, 403, "HOST_NOT_ALLOWED")
        path = scope.get("path", "")
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
