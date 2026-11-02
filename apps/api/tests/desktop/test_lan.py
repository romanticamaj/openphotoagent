""" "Allow LAN access" (08 §1.3, 02 §6.3): the API binds beyond loopback only with account login enforced."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_api.security import hash_password
from opagent_testing.clock import FakeClock

TOKEN = "t" * 64
LAN = {"Host": "192.168.1.20:47800"}


def _app(embedded_pg, clk=None, lan=True):
    return TestClient(
        create_app(
            dsn=embedded_pg.dsn,
            clock=clk or FakeClock(),
            launch_token=TOKEN,
            lan_password_hash=hash_password("correct horse") if lan else None,
        ),
        base_url="http://192.168.1.20:47800",
    )


@pytest.mark.integration
@pytest.mark.tc("DESK-015")
def test_desk_015_lan_requests_need_login(embedded_pg):
    c = _app(embedded_pg)
    assert c.get("/api/v1/capabilities", headers=LAN).status_code == 401
    assert c.post("/api/v1/auth/login", json={"password": "wrong"}, headers=LAN).status_code == 401
    r = c.post("/api/v1/auth/login", json={"password": "correct horse"}, headers=LAN)
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower()
    assert c.get("/api/v1/capabilities", headers=LAN).status_code == 200
    # a LAN browser session never reaches the worker channel, and cannot mint handoff codes
    assert c.get("/worker-api/v1/tasks/next?worker_id=w", headers=LAN).status_code == 401
    assert c.post("/api/v1/auth/handoff", headers=LAN).status_code == 401
    # cross-site requests are still refused, logged in or not
    assert c.get("/api/v1/capabilities", headers={**LAN, "Origin": "http://evil.example"}).status_code == 403
    assert c.get("/api/v1/capabilities", headers={**LAN, "Origin": "http://192.168.1.20:47800"}).status_code == 200


@pytest.mark.integration
@pytest.mark.tc("DESK-015")
def test_desk_015_without_lan_mode_foreign_hosts_are_refused(embedded_pg):
    c = _app(embedded_pg, lan=False)
    assert c.get("/api/v1/capabilities", headers=LAN).status_code == 403
    # no password login exists at all unless LAN mode is on (even for the shell's own token)
    h = {"Host": "127.0.0.1", "Authorization": f"Bearer {TOKEN}"}
    assert c.post("/api/v1/auth/login", json={"password": "x"}, headers=h).status_code == 404


@pytest.mark.integration
@pytest.mark.tc("DESK-015")
def test_desk_015_login_is_rate_limited(embedded_pg):
    clk = FakeClock()
    c = _app(embedded_pg, clk)
    codes = [c.post("/api/v1/auth/login", json={"password": "nope"}, headers=LAN).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]
    # even the right password waits out the window
    assert c.post("/api/v1/auth/login", json={"password": "correct horse"}, headers=LAN).status_code == 429
    clk.advance(timedelta(minutes=1).total_seconds() + 1)
    assert c.post("/api/v1/auth/login", json={"password": "correct horse"}, headers=LAN).status_code == 200


@pytest.mark.unit
@pytest.mark.tc("DESK-015")
def test_desk_015_password_hash_is_argon2id():
    h = hash_password("pw")
    assert h.startswith("$argon2id$") and "pw" not in h
