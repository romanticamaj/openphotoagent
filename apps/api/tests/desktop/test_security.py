"""Desktop API protection (08 §1.3): launch token, Host/Origin (DNS rebinding), handoff code, no REST root changes."""

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_testing.clock import FakeClock

TOKEN = "t" * 64
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def desk(embedded_pg):
    clk = FakeClock()
    app = create_app(dsn=embedded_pg.dsn, clock=clk, launch_token=TOKEN)
    c = TestClient(app, base_url="http://127.0.0.1:47800")
    c.clk = clk  # type: ignore[attr-defined]
    return c


@pytest.mark.integration
@pytest.mark.tc("DESK-011")
def test_desk_011_launch_token_required(desk):
    assert desk.get("/api/v1/sources").status_code == 401
    assert desk.get("/api/v1/sources", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert desk.get("/api/v1/sources", headers=AUTH).status_code == 200
    # the worker channel is protected the same way
    assert desk.post("/worker-api/v1/heartbeat", json={"worker_id": "x"}).status_code == 401


@pytest.mark.integration
@pytest.mark.tc("DESK-013")
def test_desk_013_host_and_origin_checked(desk):
    assert desk.get("/api/v1/sources", headers={**AUTH, "Host": "evil.example"}).status_code == 403
    # DNS rebinding: the attacker's name resolves to 127.0.0.1, but the Host header still names it
    assert desk.get("/api/v1/sources", headers={**AUTH, "Host": "evil.example:47800"}).status_code == 403
    assert desk.get("/api/v1/sources", headers={**AUTH, "Origin": "http://evil.example"}).status_code == 403
    for origin in ("tauri://localhost", "http://tauri.localhost", "http://127.0.0.1:47800"):
        assert desk.get("/api/v1/sources", headers={**AUTH, "Origin": origin}).status_code == 200
    assert desk.get("/api/v1/sources", headers={**AUTH, "Host": "localhost:47800"}).status_code == 200


@pytest.mark.integration
@pytest.mark.tc("DESK-016")
def test_desk_016_handoff_code_is_single_use_and_short_lived(desk):
    code = desk.post("/api/v1/auth/handoff", headers=AUTH).json()["code"]
    r = desk.get("/auth/handoff", params={"code": code}, follow_redirects=False)
    assert r.status_code in (302, 303)
    cookie = r.headers["set-cookie"]
    assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()  # attribute names are case-insensitive
    browser = TestClient(desk.app, base_url="http://127.0.0.1:47800")
    browser.cookies.set("opagent_session", r.cookies["opagent_session"])
    assert browser.get("/api/v1/sources").status_code == 200  # the cookie session works without the token
    assert desk.get("/auth/handoff", params={"code": code}, follow_redirects=False).status_code == 401  # reused
    late = desk.post("/api/v1/auth/handoff", headers=AUTH).json()["code"]
    desk.clk.advance(31)
    assert desk.get("/auth/handoff", params={"code": late}, follow_redirects=False).status_code == 401  # expired
    assert desk.post("/api/v1/auth/handoff").status_code == 401  # only the shell (with the token) can ask


@pytest.mark.integration
@pytest.mark.tc("DESK-017")
def test_desk_017_no_rest_route_adds_roots(desk):
    for method in ("post", "put", "patch", "delete"):
        r = getattr(desk, method)(
            "/api/v1/roots", headers=AUTH, **({"json": {"alias": "x", "path": "/etc"}} if method != "delete" else {})
        )
        assert r.status_code in (404, 405)
    # sources only refer to roots the user registered in the app; an absolute path is never a root
    r = desk.post("/api/v1/sources", headers=AUTH, json={"root_alias": "/etc", "sub_path": ""})
    assert r.status_code in (403, 404, 422)
    mutating = [
        (sorted(rt.methods - {"HEAD", "GET"}), rt.path)
        for rt in desk.app.routes
        if hasattr(rt, "methods") and "root" in rt.path and rt.methods - {"HEAD", "GET"}
    ]
    assert mutating == []


@pytest.mark.integration
@pytest.mark.tc("DESK-050")
def test_desk_050_the_desktop_window_may_read_the_api(desk):
    """回歸: the window's origin is http://tauri.localhost and the API is on 127.0.0.1, so every request is cross-origin.
    With no CORS headers the WebView refused them all: the installed app opened on an empty page. Tests never saw it
    because a test client is not a browser."""
    origin = "http://tauri.localhost"
    pre = desk.options(
        "/api/v1/sources",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )
    assert pre.status_code == 204  # a preflight carries no token, by design
    assert pre.headers["access-control-allow-origin"] == origin
    assert "POST" in pre.headers["access-control-allow-methods"]
    assert "authorization" in pre.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in pre.headers
    ok = desk.get("/api/v1/sources", headers={"Origin": origin, "Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == origin
    assert "Origin" in ok.headers["vary"]
    no_token = desk.get("/api/v1/sources", headers={"Origin": origin})
    assert no_token.status_code == 401 and no_token.headers["access-control-allow-origin"] == origin


@pytest.mark.integration
@pytest.mark.tc("DESK-050")
@pytest.mark.parametrize("origin", ["http://127.0.0.1:47800", "http://localhost:5173", "https://evil.example"])
def test_desk_050_no_other_origin_gets_cors(desk, origin):
    """A local web page on another port must not read the API: only the app's own window is cross-origin by design."""
    pre = desk.options("/api/v1/sources", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in pre.headers
    r = desk.get("/api/v1/sources", headers={"Origin": origin, "Authorization": f"Bearer {TOKEN}"})
    assert "access-control-allow-origin" not in r.headers
