"""MCP over Streamable HTTP with the built-in OAuth 2.1 authorization server (ADR-0015; 05 §3; 09b §8.6).

Deliberately small, on Authlib's ``AuthorizationServer`` and grant classes:

* authorization code + PKCE **S256 only**, public clients (``token_endpoint_auth_method = none``); no implicit,
  password or client-credentials grants;
* clients identify with a Client ID Metadata Document: ``client_id`` is an https URL whose JSON we fetch with SSRF
  hardening (https only, every resolved address must be public, no redirects, 64 KB, 5 s, connection pinned to the
  checked address);
* ``redirect_uri`` must match a registered one exactly, and be https or loopback http;
* the authorization response carries ``iss`` (RFC 9207); ``resource`` (RFC 8707) must name this server's ``/mcp``;
* access tokens are 15-minute JWTs bound to that audience and to a **grant** (a person's consent for one client in
  one workspace). Revoking the grant fails the very next call;
* refresh tokens are opaque and rotate on every use; presenting a used one revokes the whole grant;
* the effective scope is token ∩ the person's current role, and ``organize:apply`` is never issued (invariant 5).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
import socket
import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import jwt
from authlib.oauth2 import AuthorizationServer as _AS
from authlib.oauth2 import OAuth2Error
from authlib.oauth2.rfc6749 import ClientMixin, OAuth2Payload, OAuth2Request, grants
from authlib.oauth2.rfc6749.errors import InvalidRequestError
from authlib.oauth2.rfc7636 import CodeChallenge
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from opagent_db.schema import oauth_client, oauth_code, oauth_grant, oauth_refresh
from sqlalchemy import insert, select, update

from .state import AppState

router = APIRouter()  # control plane: discovery, authorize, token, grants
mcp_router = APIRouter()  # every workspace app: POST /mcp

ACCESS_TTL = timedelta(minutes=15)
CODE_TTL = timedelta(minutes=5)
CIMD_TTL = timedelta(hours=1)
CIMD_MAX = 64 * 1024
NEVER = {"organize:apply"}


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def issuer(st: AppState) -> str:
    return st.root.public_url.rstrip("/")


def resource(st: AppState) -> str:
    return issuer(st) + "/mcp"


def _key(st: AppState) -> str:
    """A key of its own for MCP access tokens (derived), so a worker token can never pass as one or vice versa."""
    import hmac

    return hmac.new((st.root.jwt_secret or "").encode(), b"opagent-oauth-access", hashlib.sha256).hexdigest()


def supported_scopes() -> list[str]:
    from opagent_mcp.scopes import ALL_SCOPES

    return [s for s in ALL_SCOPES if s not in NEVER]


# ---------------------------------------------------------------- CIMD (client metadata), SSRF-hardened


class CimdRejected(Exception):
    pass


def _public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_global) and not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast)


def default_resolver(host: str, port: int) -> list[str]:
    return sorted({str(ai[4][0]) for ai in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


def fetch_client_metadata(url: str, resolver: Any = None, transport: Any = None) -> dict[str, Any]:
    """Fetch and validate a Client ID Metadata Document. Raises ``CimdRejected`` before any request it would not
    make: non-https, credentials or fragments in the URL, any non-public address, redirects, > 64 KB."""
    import httpx

    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.fragment or not u.path.strip("/"):
        raise CimdRejected("client_id must be an https URL with a path")
    try:
        literal = ipaddress.ip_address(u.hostname)
    except ValueError:
        literal = None
    port = u.port or 443
    addrs = [str(literal)] if literal else (resolver or default_resolver)(u.hostname, port)
    if not addrs or not all(_public(a) for a in addrs):
        raise CimdRejected("client_id host resolves to a non-public address")
    pinned = addrs[0]
    host_for_url = f"[{pinned}]" if ":" in pinned else pinned
    target = f"https://{host_for_url}:{port}{u.path}" + (f"?{u.query}" if u.query else "")
    with (
        httpx.Client(transport=transport, timeout=5.0, follow_redirects=False) as c,
        c.stream(
            "GET",
            target,
            headers={"Host": u.netloc, "Accept": "application/json"},
            extensions={"sni_hostname": u.hostname},
        ) as r,
    ):
        if r.is_redirect or r.status_code != 200:
            raise CimdRejected(f"client metadata fetch answered {r.status_code}")
        buf = b""
        for chunk in r.iter_bytes():
            buf += chunk
            if len(buf) > CIMD_MAX:
                raise CimdRejected("client metadata larger than 64 KB")
    try:
        meta = json.loads(buf)
    except ValueError:
        raise CimdRejected("client metadata is not JSON") from None
    if not isinstance(meta, dict) or meta.get("client_id") != url:
        raise CimdRejected("client metadata client_id does not match its URL")
    uris = meta.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(x, str) and _redirect_ok(x) for x in uris):
        raise CimdRejected("redirect_uris must be https or loopback http")
    if meta.get("token_endpoint_auth_method", "none") != "none":
        raise CimdRejected("only public clients (token_endpoint_auth_method none)")
    return meta


def _redirect_ok(uri: str) -> bool:
    u = urlsplit(uri)
    if u.fragment:
        return False
    if u.scheme == "https" and u.hostname:
        return True
    return u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")


class Client(ClientMixin):  # type: ignore[misc]
    def __init__(self, client_id: str, meta: dict[str, Any]) -> None:
        self.client_id, self.meta = client_id, meta

    def get_client_id(self) -> str:
        return self.client_id

    def get_default_redirect_uri(self) -> str | None:
        return None  # always explicit

    def get_allowed_scope(self, scope: str) -> str:
        return " ".join(s for s in (scope or "").split() if s in supported_scopes())

    def check_redirect_uri(self, redirect_uri: str) -> bool:
        return redirect_uri in (self.meta.get("redirect_uris") or [])  # exact, character for character

    def check_client_secret(self, client_secret: str) -> bool:
        return False

    def check_endpoint_auth_method(self, method: str, endpoint: str) -> bool:
        return method == "none"

    def check_response_type(self, response_type: str) -> bool:
        return response_type == "code"

    def check_grant_type(self, grant_type: str) -> bool:
        return grant_type in ("authorization_code", "refresh_token")

    @property
    def domain(self) -> str:
        return urlsplit(self.client_id).hostname or self.client_id


def load_client(st: AppState, client_id: str) -> Client | None:
    root = st.root
    with root.engine.begin() as c:
        row = c.execute(select(oauth_client).where(oauth_client.c.client_id == client_id)).first()
    now = root.clock.now()
    if row is not None and now - _aware(row.fetched_at) < CIMD_TTL:
        return Client(client_id, dict(row.metadata))
    try:
        meta = fetch_client_metadata(client_id, root.oauth_resolver, root.oauth_transport)
    except (CimdRejected, ValueError, OSError) as e:
        root.oauth_rejections.append(f"{client_id[:200]}: {e}")
        return None
    except Exception as e:
        root.oauth_rejections.append(f"{client_id[:200]}: {type(e).__name__}")
        return None
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    with root.engine.begin() as c:
        c.execute(
            pg_insert(oauth_client)
            .values(client_id=client_id, metadata=meta, fetched_at=now)
            .on_conflict_do_update(index_elements=["client_id"], set_={"metadata": meta, "fetched_at": now})
        )
    return Client(client_id, meta)


def _aware(ts: Any) -> Any:
    from datetime import UTC

    return ts if ts is None or ts.tzinfo else ts.replace(tzinfo=UTC)


# ---------------------------------------------------------------- Authlib glue


class _Payload(OAuth2Payload):  # type: ignore[misc]
    def __init__(self, lists: dict[str, list[str]]) -> None:
        self._lists = defaultdict(list, lists)

    @property
    def data(self) -> dict[str, str]:
        return {k: v[0] for k, v in self._lists.items() if v}

    @property
    def datalist(self) -> defaultdict[str, list[str]]:
        return self._lists


class _Request(OAuth2Request):  # type: ignore[misc]
    def __init__(
        self, method: str, url: str, headers: dict[str, str], query: dict[str, list[str]], form: dict[str, list[str]]
    ):
        super().__init__(method=method, uri=url, headers=headers)
        self._query, self._form = query, form
        merged: dict[str, list[str]] = defaultdict(list)
        for d in (query, form):
            for k, v in d.items():
                merged[k].extend(v)
        self.payload = _Payload(dict(merged))

    @property
    def args(self) -> dict[str, str]:
        return {k: v[0] for k, v in self._query.items() if v}

    @property
    def form(self) -> dict[str, str]:
        return {k: v[0] for k, v in self._form.items() if v}


async def prepare(req: Request) -> _Request:
    q: dict[str, list[str]] = defaultdict(list)
    for k, v in req.query_params.multi_items():
        q[k].append(v)
    f: dict[str, list[str]] = defaultdict(list)
    if req.method == "POST":
        form = await req.form()
        for k, fv in form.multi_items():
            if isinstance(fv, str):
                f[k].append(fv)
    for d in (q, f):  # organize:apply is never granted: dropped from the request, not an error (ADR-0015)
        if "scope" in d:
            d["scope"] = [" ".join(x for x in v.split() if x not in NEVER) for v in d["scope"]]
    url = issuer(_st(req)) + req.url.path + (f"?{req.url.query}" if req.url.query else "")
    return _Request(req.method, url, dict(req.headers), dict(q), dict(f))


class Grantee:
    """The ``user`` Authlib carries through a grant: who, in which workspace, under which consent."""

    def __init__(self, user_id: str, grant_id: str, ws: str, scopes: list[str], res: str) -> None:
        self.id, self.grant_id, self.ws, self.scopes, self.resource = user_id, grant_id, ws, scopes, res

    def get_user_id(self) -> str:
        return self.id


class Code:
    def __init__(self, row: Any, grant: Any) -> None:
        self.row, self.grant = row, grant

    def get_redirect_uri(self) -> str:
        return str(self.row.redirect_uri)

    def get_scope(self) -> str:
        return " ".join(self.grant.scopes)

    def get_nonce(self) -> str | None:
        return self.row.nonce  # type: ignore[no-any-return]

    def get_auth_time(self) -> None:
        return None

    @property
    def code_challenge(self) -> str:
        return str(self.row.code_challenge)

    @property
    def code_challenge_method(self) -> str:
        return str(self.row.code_challenge_method)


class Refresh:
    def __init__(self, row: Any, grant: Any) -> None:
        self.row, self.grant = row, grant

    def get_scope(self) -> str:
        return " ".join(self.grant.scopes)

    def check_client(self, client: Client) -> bool:
        return bool(client.get_client_id() == self.grant.client_id)

    def is_expired(self) -> bool:
        return False

    def is_revoked(self) -> bool:
        return self.grant.revoked_at is not None


class S256Only(CodeChallenge):  # type: ignore[misc]
    """PKCE is mandatory and only S256 is accepted (OAuth 2.1)."""

    DEFAULT_CODE_CHALLENGE_METHOD = "S256"
    SUPPORTED_CODE_CHALLENGE_METHOD = ["S256"]  # noqa: RUF012 - Authlib class attribute

    def validate_code_challenge(self, grant: Any, redirect_uri: str) -> None:
        data = grant.request.payload.data
        if not data.get("code_challenge"):
            raise InvalidRequestError("Missing 'code_challenge'")
        if data.get("code_challenge_method") != "S256":
            raise InvalidRequestError("code_challenge_method must be S256")
        super().validate_code_challenge(grant, redirect_uri)

    def get_authorization_code_challenge(self, code: Code) -> str:
        return code.code_challenge

    def get_authorization_code_challenge_method(self, code: Code) -> str:
        return code.code_challenge_method


def revoke_grant(st: AppState, grant_id: str, reason: str) -> None:
    with st.root.engine.begin() as c:
        c.execute(
            update(oauth_grant)
            .where(oauth_grant.c.id == grant_id, oauth_grant.c.revoked_at.is_(None))
            .values(revoked_at=st.root.clock.now(), revoked_reason=reason)
        )


def _grant(c: Any, gid: str) -> Any:
    return c.execute(select(oauth_grant).where(oauth_grant.c.id == gid)).first()


class CodeGrant(grants.AuthorizationCodeGrant):  # type: ignore[misc]
    TOKEN_ENDPOINT_AUTH_METHODS = ["none"]  # noqa: RUF012 - Authlib class attribute

    def save_authorization_code(self, code: str, request: Any) -> None:
        st: AppState = self.server.st
        g: Grantee = request.user
        data = request.payload.data
        with st.root.engine.begin() as c:
            c.execute(
                insert(oauth_code).values(
                    code_hash=_sha(code),
                    grant_id=g.grant_id,
                    redirect_uri=data.get("redirect_uri"),
                    code_challenge=data.get("code_challenge"),
                    code_challenge_method=data.get("code_challenge_method"),
                    nonce=data.get("nonce"),
                    expires_at=st.root.clock.now() + CODE_TTL,
                )
            )

    def query_authorization_code(self, code: str, client: Client) -> Code | None:
        st: AppState = self.server.st
        with st.root.engine.begin() as c:
            row = c.execute(select(oauth_code).where(oauth_code.c.code_hash == _sha(code)).with_for_update()).first()
            if row is None:
                return None
            g = _grant(c, row.grant_id)
            if g is None or g.client_id != client.get_client_id():
                return None
            if row.used_at is not None:  # a code used twice: someone has a copy → kill what it issued
                c.execute(
                    update(oauth_grant)
                    .where(oauth_grant.c.id == g.id, oauth_grant.c.revoked_at.is_(None))
                    .values(revoked_at=st.root.clock.now(), revoked_reason="code_reuse")
                )
                return None
            if _aware(row.expires_at) <= st.root.clock.now() or g.revoked_at is not None:
                return None
            c.execute(
                update(oauth_code).where(oauth_code.c.code_hash == row.code_hash).values(used_at=st.root.clock.now())
            )
        res = self.request.payload.data.get("resource")
        if res is not None and res != g.resource:
            return None
        return Code(row, g)

    def delete_authorization_code(self, authorization_code: Code) -> None:
        return None  # kept, marked used: a second use is detected above

    def authenticate_user(self, authorization_code: Code) -> Grantee:
        g = authorization_code.grant
        return Grantee(g.user_id, g.id, g.workspace_id, list(g.scopes), g.resource)


class RefreshGrant(grants.RefreshTokenGrant):  # type: ignore[misc]
    TOKEN_ENDPOINT_AUTH_METHODS = ["none"]  # noqa: RUF012 - Authlib class attribute
    INCLUDE_NEW_REFRESH_TOKEN = True

    def authenticate_refresh_token(self, refresh_token: str) -> Refresh | None:
        st: AppState = self.server.st
        with st.root.engine.begin() as c:
            row = c.execute(
                select(oauth_refresh).where(oauth_refresh.c.token_hash == _sha(refresh_token)).with_for_update()
            ).first()
            if row is None:
                return None
            g = _grant(c, row.grant_id)
            if g is None or g.revoked_at is not None:
                return None
            if row.used_at is not None:  # rotated away and shown again: the family is compromised
                c.execute(
                    update(oauth_grant)
                    .where(oauth_grant.c.id == g.id)
                    .values(revoked_at=st.root.clock.now(), revoked_reason="refresh_reuse")
                )
                return None
            c.execute(
                update(oauth_refresh)
                .where(oauth_refresh.c.token_hash == row.token_hash)
                .values(used_at=st.root.clock.now())
            )
        res = self.request.payload.data.get("resource")
        if res is not None and res != g.resource:
            return None
        return Refresh(row, g)

    def authenticate_user(self, refresh: Refresh) -> Grantee:
        g = refresh.grant
        return Grantee(g.user_id, g.id, g.workspace_id, list(g.scopes), g.resource)

    def revoke_old_credential(self, refresh: Refresh) -> None:
        return None  # marked used in authenticate_refresh_token


class Server(_AS):  # type: ignore[misc]
    def __init__(self, st: AppState) -> None:
        super().__init__(scopes_supported=supported_scopes())
        self.st = st
        self.register_token_generator("default", self._tokens)
        self.register_grant(CodeGrant, [S256Only(required=True)])
        self.register_grant(RefreshGrant)

    def query_client(self, client_id: str) -> Client | None:
        return load_client(self.st, client_id) if client_id else None

    def save_token(self, token: dict[str, Any], request: Any) -> None:
        rt = token.get("refresh_token")
        if rt:
            with self.st.root.engine.begin() as c:
                c.execute(
                    insert(oauth_refresh).values(
                        token_hash=_sha(rt), grant_id=request.user.grant_id, created_at=self.st.root.clock.now()
                    )
                )

    def _tokens(
        self,
        grant_type: str,
        client: Client,
        user: Grantee | None = None,
        scope: str | None = None,
        expires_in: int | None = None,
        include_refresh_token: bool = True,
    ) -> dict[str, Any]:
        assert user is not None
        now = int(self.st.root.clock.now().timestamp())
        ttl = int(ACCESS_TTL.total_seconds())
        scopes = [s for s in (scope or "").split() if s in user.scopes and s not in NEVER]
        claims = {
            "iss": issuer(self.st),
            "aud": user.resource,
            "sub": user.id,
            "client_id": client.get_client_id(),
            "grant": user.grant_id,
            "ws": user.ws,
            "scope": " ".join(scopes),
            "iat": now,
            "exp": now + ttl,
            "jti": secrets.token_hex(8),
        }
        out = {
            "token_type": "Bearer",
            "access_token": jwt.encode(claims, _key(self.st), algorithm="HS256"),
            "expires_in": ttl,
            "scope": " ".join(scopes),
        }
        if include_refresh_token:
            out["refresh_token"] = "opagent_ort_" + secrets.token_urlsafe(32)
        return out

    def create_oauth2_request(self, request: Any) -> Any:
        return request

    def create_json_request(self, request: Any) -> Any:
        return request

    def handle_response(self, status_code: int, payload: Any, headers: list[tuple[str, str]]) -> Response:
        h = dict(headers)
        if isinstance(payload, dict):
            return JSONResponse(payload, status_code=status_code, headers=h)
        return Response(payload, status_code=status_code, headers=h)

    def send_signal(self, name: str, *args: Any, **kwargs: Any) -> None:
        return None


def server(st: AppState) -> Server:
    root = st.root
    if root.oauth_server is None:
        root.oauth_server = Server(root)
    return root.oauth_server  # type: ignore[no-any-return]


# ---------------------------------------------------------------- discovery


def _prm(st: AppState) -> dict[str, Any]:
    return {
        "resource": resource(st),
        "authorization_servers": [issuer(st)],
        "scopes_supported": supported_scopes(),
        "bearer_methods_supported": ["header"],
        "resource_name": "OpenPhotoAgent",
    }


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource(req: Request) -> dict[str, Any]:
    """RFC 9728 Protected Resource Metadata."""
    return _prm(_st(req))


@router.get("/.well-known/oauth-authorization-server")
def as_metadata(req: Request) -> dict[str, Any]:
    """RFC 8414 Authorization Server Metadata."""
    st = _st(req)
    iss = issuer(st)
    return {
        "issuer": iss,
        "authorization_endpoint": iss + "/oauth/authorize",
        "token_endpoint": iss + "/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": supported_scopes(),
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
    }


def www_authenticate(st: AppState, error: str | None = None, scope: str | None = None) -> str:
    parts = [f'resource_metadata="{issuer(st)}/.well-known/oauth-protected-resource"']
    if error:
        parts.append(f'error="{error}"')
    if scope:
        parts.append(f'scope="{scope}"')
    return "Bearer " + ", ".join(parts)


# ---------------------------------------------------------------- authorize (consent) and token


def _session_user(st: AppState, req: Request) -> dict[str, Any] | None:
    from .multiuser import _cookie, session_user

    sid = _cookie(req.headers.get("cookie", ""))
    return session_user(st.root, sid) if sid else None


def _validate(st: AppState, oreq: _Request) -> tuple[Any, Client]:
    srv = server(st)
    data = oreq.payload.data
    if data.get("resource") != resource(st):
        raise InvalidRequestError("resource must be " + resource(st))
    grant = srv.get_consent_grant(request=oreq)  # client, redirect_uri (exact), PKCE S256
    return grant, grant.client


def _requested(st: AppState, user: dict[str, Any], ws: str, scope: str) -> list[str]:
    from .permissions import capped_scopes, permissions

    asked = [s for s in (scope or "photos:read").split() if s in supported_scopes()]
    return capped_scopes(asked, permissions(user["roles"], ws))


@router.get("/oauth/authorize")
async def authorize_page(req: Request) -> Any:
    """The consent screen's data: the client's domain (not only its self-declared name) and what it will get."""
    from starlette.concurrency import run_in_threadpool

    st = _st(req)
    oreq = await prepare(req)
    user = await run_in_threadpool(_session_user, st, req)
    if user is None:
        return JSONResponse({"error": "login_required", "login": "/login?next=" + req.url.path}, status_code=401)
    try:
        _, client = await run_in_threadpool(_validate, st, oreq)
    except OAuth2Error as e:
        return JSONResponse({"error": e.error, "error_description": e.description}, status_code=400)
    ws = oreq.payload.data.get("workspace") or "default"
    from .permissions import permissions

    if not permissions(user["roles"], ws):
        return JSONResponse({"error": "access_denied", "error_description": "no such workspace"}, status_code=400)
    return {
        "client_id": client.client_id,
        "client_domain": client.domain,
        "client_name": str(client.meta.get("client_name") or client.domain)[:100],
        "workspace": ws,
        "scopes": _requested(st, user, ws, oreq.payload.data.get("scope") or ""),
        "never": sorted(NEVER),
        "csrf": user["csrf"],
    }


@router.post("/oauth/authorize")
async def authorize(req: Request) -> Any:
    """The person approved (or denied) on the consent screen; issues the code to the exact redirect URI."""
    from starlette.concurrency import run_in_threadpool

    st = _st(req)
    oreq = await prepare(req)
    user = await run_in_threadpool(_session_user, st, req)
    if user is None:
        return JSONResponse({"error": "login_required"}, status_code=401)
    import hmac

    if not hmac.compare_digest((oreq.form.get("csrf") or "").encode(), user["csrf"].encode()):
        return JSONResponse({"error": "csrf"}, status_code=403)

    def go() -> Any:
        srv = server(st)
        try:
            _, client = _validate(st, oreq)
        except OAuth2Error as e:
            return JSONResponse({"error": e.error, "error_description": e.description}, status_code=400)
        data = oreq.payload.data
        ws = data.get("workspace") or "default"
        from .permissions import permissions

        if not permissions(user["roles"], ws) or st.workspace_db(ws) is None:
            return JSONResponse({"error": "access_denied"}, status_code=400)
        if oreq.form.get("decision") != "approve":
            q = {"error": "access_denied", "iss": issuer(st)}
            if data.get("state"):
                q["state"] = data["state"]
            return RedirectResponse(data["redirect_uri"] + "?" + urlencode(q), status_code=303)
        scopes = _requested(st, user, ws, data.get("scope") or "")
        gid = str(uuid.uuid4())
        with st.root.engine.begin() as c:
            c.execute(
                insert(oauth_grant).values(
                    id=gid,
                    user_id=user["id"],
                    client_id=client.client_id,
                    workspace_id=ws,
                    scopes=scopes,
                    resource=resource(st),
                    created_at=st.root.clock.now(),
                )
            )
        resp = srv.create_authorization_response(
            request=oreq, grant_user=Grantee(user["id"], gid, ws, scopes, resource(st))
        )
        loc = resp.headers.get("location")
        if loc:  # RFC 9207: tell the client who answered
            sep = "&" if "?" in loc else "?"
            resp.headers["location"] = loc + sep + urlencode({"iss": issuer(st)})
            resp.status_code = 303
        return resp

    return await run_in_threadpool(go)


@router.post("/oauth/token")
async def token(req: Request) -> Any:
    from starlette.concurrency import run_in_threadpool

    st = _st(req)
    oreq = await prepare(req)
    res = oreq.payload.data.get("resource")
    if res is not None and res != resource(st):
        return JSONResponse({"error": "invalid_target"}, status_code=400)
    return await run_in_threadpool(server(st).create_token_response, oreq)


@router.get("/api/v1/oauth/grants")
def list_grants(req: Request) -> list[dict[str, Any]]:
    st = _st(req)
    u = req.state.opa_user
    with st.root.engine.begin() as c:
        rows = c.execute(
            select(oauth_grant).where(oauth_grant.c.user_id == u["id"]).order_by(oauth_grant.c.created_at)
        ).all()
    return [
        {
            "id": r.id,
            "client_id": r.client_id,
            "client_domain": urlsplit(r.client_id).hostname,
            "workspace": r.workspace_id,
            "scopes": list(r.scopes),
            "revoked": r.revoked_at is not None,
        }
        for r in rows
    ]


@router.delete("/api/v1/oauth/grants/{grant_id}")
def delete_grant(grant_id: str, req: Request) -> dict[str, Any]:
    st = _st(req)
    u = req.state.opa_user
    with st.root.engine.begin() as c:
        g = _grant(c, grant_id)
    if g is None or (g.user_id != u["id"] and "mcp:manage" not in req.state.opa_perms):
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    revoke_grant(st, grant_id, "revoked_by_user")
    return {"revoked": grant_id}


# ---------------------------------------------------------------- the resource server: POST /mcp


def verify_access(st: AppState, tok: str) -> dict[str, Any] | None:
    """Claims of a valid MCP access token for this server, with its grant still live; else ``None``."""
    try:
        claims = jwt.decode(
            tok,
            _key(st),
            algorithms=["HS256"],
            audience=resource(st),
            issuer=issuer(st),
            options={"verify_exp": False, "require": ["sub", "aud", "exp", "grant", "ws", "scope"]},
        )
    except jwt.PyJWTError:
        return None
    if claims["exp"] <= st.root.clock.now().timestamp():
        return None
    with st.root.engine.begin() as c:
        g = _grant(c, str(claims["grant"]))
    if g is None or g.revoked_at is not None or g.user_id != claims["sub"] or g.workspace_id != claims["ws"]:
        return None
    return dict(claims)


class OAuthToken:
    """What ``mcp_server`` needs from a credential, for an OAuth grant."""

    def __init__(self, claims: dict[str, Any], scopes: frozenset[str]) -> None:
        self.id = "oauth:" + str(claims["grant"])
        self.seq = 0
        self.name = urlsplit(str(claims["client_id"])).hostname or "MCP client"
        self.client = self.name
        self.scopes = scopes
        self.folders: list[dict[str, str]] = []
        self.media_policy: str | None = None
        self.owner: str | None = str(claims["sub"])

    @property
    def scope_key(self) -> str:
        return "*"


def _same_site(st: AppState, req: Request) -> bool:
    """The request names this server: Host (or the reverse proxy's X-Forwarded-Host) is the public address, and a
    browser's Origin, when present, is that same origin. Native MCP clients send no Origin."""
    u = urlsplit(issuer(st))
    host = (req.headers.get("x-forwarded-host") or req.headers.get("host") or "").split(",")[0].strip().lower()
    if host != (u.netloc or "").lower():
        return False
    origin = req.headers.get("origin")
    return origin is None or origin.rstrip("/").lower() == f"{u.scheme}://{u.netloc}".lower()


@mcp_router.post("/mcp")
async def mcp(req: Request) -> Any:
    """Streamable HTTP (JSON responses). 401 points at the Protected Resource Metadata (RFC 9728)."""
    from starlette.concurrency import run_in_threadpool

    from . import mcp_server
    from .multiuser import user_permissions
    from .permissions import SCOPE_PERMISSION

    st = _st(req)
    if not _same_site(st, req):  # MCP Streamable HTTP: validate Origin and Host (DNS rebinding, MCP-TRN-007)
        return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
    if not await run_in_threadpool(mcp_server.mcp_enabled, st):  # every transport honours mcp.enabled (PF-060)
        return mcp_server.disabled_response()
    auth = req.headers.get("authorization", "")
    claims = await run_in_threadpool(verify_access, st, auth[7:]) if auth.startswith("Bearer ") else None
    if claims is None or claims["ws"] != st.ws_id:
        return JSONResponse(
            {"error": "invalid_token"},
            status_code=401,
            headers={"WWW-Authenticate": www_authenticate(st, "invalid_token" if auth else None)},
        )
    perms = await run_in_threadpool(user_permissions, st, str(claims["sub"]), st.ws_id)
    scopes = frozenset(s for s in str(claims["scope"]).split() if s not in NEVER and SCOPE_PERMISSION.get(s) in perms)
    try:
        msg = await req.json()
    except ValueError:
        return JSONResponse(mcp_server._rpc_error(None, -32700, "parse error"))
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return JSONResponse(mcp_server._rpc_error(None, -32600, "invalid request"))
    if msg.get("method") == "tools/call":
        tool = mcp_server.BY_NAME.get(  # type: ignore[attr-defined]
            str((msg.get("params") or {}).get("name") or "")
        )
        if tool is not None and tool.scope not in scopes:
            return JSONResponse(
                mcp_server._rpc_error(msg.get("id"), -32003, "insufficient_scope", {"scope": tool.scope}),
                status_code=403,
                headers={"WWW-Authenticate": www_authenticate(st, "insufficient_scope", tool.scope)},
            )
    out = await run_in_threadpool(mcp_server.handle, st, OAuthToken(claims, scopes), msg, req)  # type: ignore[arg-type]
    if out is None:
        return JSONResponse(status_code=202, content=None)
    return JSONResponse(out)
