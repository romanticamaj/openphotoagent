"""Multi-user RBAC and login (02 §1.3, §6.3; 09b §8.2)."""

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from opagent_api import permissions as P
from opagent_api.app import create_app
from opagent_db.schema import mcp_token, source
from opagent_mcp.scopes import READONLY_SCOPES
from opagent_mcp.tools import TOOLS
from opagent_testing.clock import FakeClock
from sqlalchemy import insert, select, update

ROLES = ["org_owner", "org_admin", "ws_admin", "ws_editor", "ws_reviewer", "ws_viewer"]
OPEN = {"/api/v1/auth/login", "/api/v1/auth/oidc/start", "/api/v1/auth/oidc/callback"}


def write_routes(app):
    out = []
    for path, ops in app.openapi()["paths"].items():
        for m in ops:
            if m.upper() not in P.SAFE and path.startswith("/api/v1") and path not in OPEN:
                out.append((m.upper(), path))
    return sorted(out)


def concrete(path):
    return re.sub(r"\{[^}]+\}", "x1", path)


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-RBAC-001")
def test_rbac_001_viewer_cannot_write(cp_app, as_role):
    routes = write_routes(cp_app)
    assert len(routes) > 80
    unmapped = [r for r in routes if r not in P.ROUTES]
    assert unmapped == []  # every write endpoint declares its permission
    v = as_role("ws_viewer")
    for method, path in routes:
        if P.ROUTES[(method, path)] in P.VIEWER:
            continue  # read-only POSTs (search, preflight, estimates …)
        r = v.request(method, concrete(path), json={})
        assert r.status_code == 403, (method, path, r.status_code, r.text)
        assert r.json()["code"] == "FORBIDDEN_ROLE"
    # MCP: a viewer cannot mint write scopes, and a demoted member's old token cannot use them
    r = v.post("/api/v1/mcp/tokens", json={"name": "x", "scopes": ["photos:read", "labels:write"]})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_ROLE"
    e = as_role("ws_editor")
    every = sorted({t.scope for t in TOOLS} - {"organize:apply", "faces:read", "faces:media"})
    r = e.post("/api/v1/mcp/tokens", json={"name": "x", "scopes": every})
    assert r.status_code == 201, r.text
    pat = r.json()["token"]
    st = cp_app.state.opa
    with st.engine.begin() as c:
        uid = c.execute(select(mcp_token.c.owner_user_id).where(mcp_token.c.id == r.json()["id"])).scalar()
        assert uid
        c.execute(update(P_membership()).where(P_membership().c.user_id == uid).values(role="ws_viewer"))
    m = TestClient(cp_app, base_url="https://testserver", headers={"Authorization": f"Bearer {pat}"})
    writes = [t for t in TOOLS if t.scope not in READONLY_SCOPES and t.scope in every and t.available_from <= "0.3.0"]
    assert writes
    for t in writes:
        r = m.post(
            "/mcp/v1/rpc",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": t.name, "arguments": {}}},
        )
        assert r.status_code == 403, (t.name, r.text)
        assert r.json()["error"]["data"]["code"] == "FORBIDDEN_ROLE"


def P_membership():
    from opagent_db.schema import membership

    return membership


MATRIX = {  # 02 §1.3: create job · approve apply · manage workers · manage members · review
    "org_owner": (1, 1, 1, 1, 1),
    "org_admin": (0, 0, 1, 1, 0),
    "ws_admin": (1, 1, 0, 0, 1),
    "ws_editor": (1, 0, 0, 0, 1),
    "ws_reviewer": (0, 0, 0, 0, 1),
    "ws_viewer": (0, 0, 0, 0, 0),
}
ACTIONS = [
    ("job:create", "POST", "/api/v1/jobs"),
    ("output:apply", "POST", "/api/v1/output-plans/p1/approve"),
    ("worker:manage", "POST", "/api/v1/workers/enrollment-codes"),
    ("member:manage", "POST", "/api/v1/members"),
    ("review:write", "POST", "/api/v1/reviews/r1"),
]


@pytest.mark.integration
@pytest.mark.tc("MU-RBAC-002")
@pytest.mark.parametrize("role", ROLES)
def test_rbac_002_role_matrix(cp_app, as_role, role):
    c = as_role(role)
    for (perm, method, path), want in zip(ACTIONS, MATRIX[role], strict=True):
        assert P.can(role, perm) == bool(want), (role, perm)
        r = c.request(method, path, json={})
        assert (r.status_code != 403) == bool(want), (role, path, r.status_code, r.text)


@pytest.mark.integration
@pytest.mark.tc("MU-RBAC-003")
def test_rbac_003_apply_needs_output_apply(cp_app, as_role):
    ed, adm = as_role("ws_editor"), as_role("ws_admin")
    for path in ("/api/v1/output-plans/p1/approve", "/api/v1/approvals/a1/decision", "/api/v1/output-plans/p1/undo"):
        r = ed.post(path, json={"decision": "approve"})
        assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_ROLE"
        assert adm.post(path, json={"decision": "approve"}).status_code in (404, 409, 422)  # authorised; no such plan


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-AUTH-001")
def test_auth_001_password_login(cp_app):
    c = TestClient(cp_app, base_url="https://testserver")
    assert c.get("/api/v1/sources").status_code == 401
    for _ in range(5):
        r = c.post("/api/v1/auth/login", json={"email": "admin@example.invalid", "password": "wrong"})
        assert r.status_code == 401 and r.json()["code"] == "LOGIN_FAILED"
    r = c.post("/api/v1/auth/login", json={"email": "admin@example.invalid", "password": "correct horse battery"})
    assert r.status_code == 429  # rate-limited even with the right password
    cp_app.clock.advance(61)
    r = c.post("/api/v1/auth/login", json={"email": "ADMIN@example.invalid", "password": "correct horse battery"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie.replace("Lax", "lax")
    csrf = r.json()["csrf"]
    with cp_app.state.opa.engine.begin() as conn:
        from opagent_db.schema import app_user, user_session

        ph = conn.execute(select(app_user.c.password_hash)).scalars().all()
        assert all(h.startswith("$argon2id$") for h in ph)
        sid = cookie.split("=", 1)[1].split(";", 1)[0]
        assert conn.execute(select(user_session).where(user_session.c.id_hash == sid)).first() is None  # hashed
    assert c.get("/api/v1/sources").status_code == 200
    r = c.post("/api/v1/sources", json={}, headers={"X-OPA-UI": "1"})
    assert r.status_code == 403 and r.json()["code"] == "CSRF_CHECK_FAILED"
    assert c.post("/api/v1/sources", json={}, headers={"X-OPA-UI": "1", "X-CSRF-Token": csrf}).status_code != 403
    assert c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert c.get("/api/v1/sources").status_code == 401


@pytest.mark.integration
@pytest.mark.tc("MU-AUTH-003")
def test_auth_003_single_user_upgrade(embedded_pg):
    clock = FakeClock(datetime(2026, 11, 2, 12, 0, tzinfo=UTC))
    single = create_app(dsn=embedded_pg.dsn, clock=clock)
    with single.state.opa.engine.begin() as c:
        c.execute(insert(source).values(id="s1", workspace_id="default", root_alias="photos", include=[], exclude=[]))
    assert TestClient(single).get("/api/v1/sources").json()
    single.state.opa.engine.dispose()
    with pytest.raises(ValueError, match="owner"):
        create_app(dsn=embedded_pg.dsn, clock=clock, multi_user=True, jwt_secret="s" * 32)  # nobody could log in
    app = create_app(
        dsn=embedded_pg.dsn,
        clock=clock,
        multi_user=True,
        jwt_secret="s" * 32,
        owner=("me@example.invalid", "pw-123456"),
    )
    anon = TestClient(app, base_url="https://testserver")
    assert anon.get("/api/v1/sources").status_code == 401  # login is now required everywhere
    r = anon.post("/api/v1/auth/login", json={"email": "me@example.invalid", "password": "pw-123456"})
    assert r.status_code == 200
    assert [s["id"] for s in anon.get("/api/v1/sources").json()] == ["s1"]  # data kept
    me = anon.get("/api/v1/auth/me").json()
    assert me["roles"] == {"*": "org_owner"}
    # a second start keeps the owner and does not create another
    app2 = create_app(
        dsn=embedded_pg.dsn, clock=clock, multi_user=True, jwt_secret="s" * 32, owner=("x@example.invalid", "y")
    )
    with app2.state.opa.engine.begin() as c:
        from opagent_db.schema import app_user

        assert c.execute(select(app_user.c.email)).scalars().all() == ["me@example.invalid"]
