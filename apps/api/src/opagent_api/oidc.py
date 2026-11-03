"""OIDC sign-in for the control plane (02 ADR-006, §6.3; 09b §8.2 MU-AUTH-002).

Authorization code + PKCE (S256) with ``state`` and ``nonce``; the ID token is verified against the provider's JWKS
(RS256/ES256, ``kid``; an unknown ``kid`` refetches the JWKS once, so key rotation just works). Nobody signs up by
signing in: the first OIDC login must match an invited member's e-mail (verified by the IdP), after which the
account is bound to the provider's ``sub``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from opagent_db.schema import app_user
from sqlalchemy import select, update

from .state import AppState

router = APIRouter()
PENDING_TTL = timedelta(minutes=10)
LEEWAY_S = 60


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def _cfg(st: AppState) -> dict[str, Any]:
    if not st.oidc:
        raise HTTPException(404, detail={"code": "OIDC_NOT_CONFIGURED"})
    return st.oidc


def _http(st: AppState) -> httpx.Client:
    return httpx.Client(transport=st.oidc_transport, timeout=10.0)


def discovery(st: AppState) -> dict[str, Any]:
    if st.oidc_cache.get("discovery") is None:
        cfg = _cfg(st)
        with _http(st) as c:
            r = c.get(cfg["issuer"].rstrip("/") + "/.well-known/openid-configuration")
        r.raise_for_status()
        d = r.json()
        if d.get("issuer") != cfg["issuer"]:
            raise HTTPException(502, detail={"code": "OIDC_DISCOVERY_MISMATCH"})
        st.oidc_cache["discovery"] = d
    return st.oidc_cache["discovery"]  # type: ignore[no-any-return]


def _jwks(st: AppState, refresh: bool = False) -> dict[str, Any]:
    if refresh or st.oidc_cache.get("jwks") is None:
        with _http(st) as c:
            r = c.get(discovery(st)["jwks_uri"])
        r.raise_for_status()
        st.oidc_cache["jwks"] = {k["kid"]: k for k in r.json().get("keys", []) if "kid" in k}
    return st.oidc_cache["jwks"]  # type: ignore[no-any-return]


def _callback_url(st: AppState) -> str:
    return st.public_url.rstrip("/") + "/api/v1/auth/oidc/callback"


@router.get("/auth/oidc/start")
def start(req: Request) -> RedirectResponse:
    st = _st(req)
    cfg = _cfg(st)
    d = discovery(st)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    now = st.clock.now()
    for k in [k for k, v in st.oidc_pending.items() if v["exp"] <= now]:
        st.oidc_pending.pop(k, None)
    st.oidc_pending[state] = {"nonce": nonce, "verifier": verifier, "exp": now + PENDING_TTL}
    q = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": _callback_url(st),
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse(d["authorization_endpoint"] + "?" + urlencode(q), status_code=303)


def _verify(st: AppState, id_token: str, nonce: str) -> dict[str, Any]:
    cfg = _cfg(st)
    try:
        kid = jwt.get_unverified_header(id_token).get("kid")
    except jwt.PyJWTError:
        raise HTTPException(401, detail={"code": "OIDC_TOKEN_INVALID"}) from None
    keys = _jwks(st)
    if kid not in keys:  # the provider rotated its keys
        keys = _jwks(st, refresh=True)
    if kid not in keys:
        raise HTTPException(401, detail={"code": "OIDC_TOKEN_INVALID"})
    key = jwt.PyJWK(keys[kid])
    try:
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256", "ES256"],
            audience=cfg["client_id"],
            issuer=cfg["issuer"],
            options={"verify_exp": False, "require": ["iss", "aud", "sub", "exp", "iat"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(401, detail={"code": "OIDC_TOKEN_INVALID"}) from None
    if claims["exp"] + LEEWAY_S <= st.clock.now().timestamp():
        raise HTTPException(401, detail={"code": "OIDC_TOKEN_EXPIRED"})
    if not secrets.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise HTTPException(401, detail={"code": "OIDC_NONCE_INVALID"})
    return dict(claims)


@router.get("/auth/oidc/callback")
def callback(req: Request, code: str = "", state: str = "") -> Any:
    from .multiuser import open_session

    st = _st(req)
    cfg = _cfg(st)
    pend = st.oidc_pending.pop(state, None) if state else None
    if pend is None or pend["exp"] <= st.clock.now():
        raise HTTPException(400, detail={"code": "OIDC_STATE_INVALID"})
    with _http(st) as c:
        r = c.post(
            discovery(st)["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _callback_url(st),
                "client_id": cfg["client_id"],
                "client_secret": cfg.get("client_secret", ""),
                "code_verifier": pend["verifier"],
            },
        )
    if r.status_code != 200 or "id_token" not in r.json():
        raise HTTPException(401, detail={"code": "OIDC_CODE_REJECTED"})
    claims = _verify(st, r.json()["id_token"], pend["nonce"])
    sub = f"{cfg['issuer']}|{claims['sub']}"
    with st.engine.begin() as c:
        u = c.execute(select(app_user).where(app_user.c.oidc_sub == sub)).first()
        if u is None:
            email = str(claims.get("email") or "").strip().lower()
            if not email or claims.get("email_verified") is not True:
                raise HTTPException(403, detail={"code": "OIDC_EMAIL_UNVERIFIED"})
            u = c.execute(select(app_user).where(app_user.c.email == email, app_user.c.oidc_sub.is_(None))).first()
            if u is None:  # only invited people get in (02 §1.3: members are added by an admin)
                raise HTTPException(403, detail={"code": "OIDC_NOT_INVITED"})
            c.execute(update(app_user).where(app_user.c.id == u.id).values(oidc_sub=sub))
        if u.disabled:
            raise HTTPException(403, detail={"code": "ACCOUNT_DISABLED"})
    session = open_session(st, u.id)
    out = RedirectResponse("/", status_code=303)
    out.headers["set-cookie"] = session.headers["set-cookie"]
    return out
