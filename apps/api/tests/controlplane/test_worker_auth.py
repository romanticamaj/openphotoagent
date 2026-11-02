"""Remote worker pairing and tokens (02 §5.2, §6.3; 09b §8.1/§8.4): one-time enrollment codes, an ed25519 key that
never leaves the worker, short-lived JWTs bound to the worker, and the attacks that must fail."""

import base64
import time
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_api.multiuser import create_user
from opagent_testing.clock import FakeClock


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


class Key:
    def __init__(self):
        self.sk = Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization as s

        self.pub = b64(self.sk.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw))

    def sign(self, msg: str) -> str:
        return b64(self.sk.sign(msg.encode()))


@pytest.fixture
def cp(embedded_pg):
    clock = FakeClock(datetime(2026, 11, 2, 12, 0, tzinfo=UTC))
    app = create_app(dsn=embedded_pg.dsn, clock=clock, multi_user=True, jwt_secret="test-secret-" + "x" * 32)
    c = TestClient(app)
    create_user(app.state.opa, "admin@example.invalid", "correct horse battery", role="org_owner")
    r = c.post("/api/v1/auth/login", json={"email": "admin@example.invalid", "password": "correct horse battery"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    c.headers["X-OPA-UI"] = "1"
    c.clock = clock
    return c


def code(cp):
    r = cp.post("/api/v1/workers/enrollment-codes", json={"name": "gpu-desktop"})
    assert r.status_code == 201, r.text
    assert r.json()["code"].startswith("OPA-")
    return r.json()["code"]


def enroll(cp, k, c):
    return TestClient(cp.app).post(
        "/worker-api/v1/enroll", json={"code": c, "public_key": k.pub, "name": "gpu-desktop", "protocol_version": "1.0"}
    )


def token(cp, wid, refresh, k, nonce=None, ts=None):
    ts = ts or cp.clock.now().isoformat()
    nonce = nonce or base64.b16encode(time.time_ns().to_bytes(8, "big")).decode()
    return TestClient(cp.app).post(
        "/worker-api/v1/token",
        json={"worker_id": wid, "ts": ts, "nonce": nonce, "sig": k.sign(f"{wid}|{ts}|{nonce}")},
        headers={"Authorization": f"Bearer {refresh}"},
    )


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-WORKER-002")
def test_worker_002_one_time_codes(cp):
    k = Key()
    c = code(cp)
    r = enroll(cp, k, c)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["worker_id"] and body["refresh_token"] and "server_pubkey_pin" in body
    assert enroll(cp, Key(), c).json()["code"] == "ENROLL_CODE_INVALID"  # used
    old = code(cp)
    cp.clock.advance(11 * 60)
    assert enroll(cp, Key(), old).json()["code"] == "ENROLL_CODE_INVALID"  # expired after 10 minutes
    for _ in range(5):
        assert enroll(cp, Key(), "OPA-AAAA-BBBB").status_code == 401
    fresh = code(cp)
    assert enroll(cp, Key(), fresh).status_code == 429  # guessing is rate-limited, even a right code waits
    assert TestClient(cp.app).post("/api/v1/workers/enrollment-codes", json={"name": "x"}).status_code == 401


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-WORKER-003", "MU-SEC-002")
def test_worker_003_signed_token_exchange_and_replay(cp):
    k = Key()
    e = enroll(cp, k, code(cp)).json()
    wid, refresh = e["worker_id"], e["refresh_token"]
    r = token(cp, wid, refresh, k, nonce="n-1")
    assert r.status_code == 200, r.text
    claims = jwt.decode(r.json()["access_token"], options={"verify_signature": False})
    assert claims["aud"] == "worker" and claims["sub"] == wid and claims["exp"] - claims["iat"] == 15 * 60
    assert token(cp, wid, refresh, k, nonce="n-1").json()["code"] == "NONCE_REPLAYED"
    skew = (cp.clock.now() - timedelta(minutes=6)).isoformat()
    assert token(cp, wid, refresh, k, ts=skew).json()["code"] == "CLOCK_SKEW"
    assert token(cp, wid, refresh, Key()).json()["code"] == "SIGNATURE_INVALID"  # someone else's key
    assert token(cp, wid, "not-the-refresh-token", k).status_code == 401
    access = token(cp, wid, refresh, k).json()["access_token"]
    w = TestClient(cp.app, headers={"Authorization": f"Bearer {access}"})
    assert w.post("/worker-api/v1/heartbeat", json={"worker_id": wid}).status_code == 200
    cp.clock.advance(16 * 60)
    assert w.post("/worker-api/v1/heartbeat", json={"worker_id": wid}).status_code == 401  # expired
    access = token(cp, wid, refresh, k).json()["access_token"]
    w = TestClient(cp.app, headers={"Authorization": f"Bearer {access}"})
    assert cp.delete(f"/api/v1/workers/{wid}").status_code == 200  # revoke
    assert w.post("/worker-api/v1/heartbeat", json={"worker_id": wid}).status_code == 401  # next message refused
    assert token(cp, wid, refresh, k).status_code == 401  # refresh token dead too


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-SEC-001")
def test_sec_001_worker_impersonation(cp):
    ka, kb = Key(), Key()
    a = enroll(cp, ka, code(cp)).json()
    b = enroll(cp, kb, code(cp)).json()
    acc_a = token(cp, a["worker_id"], a["refresh_token"], ka).json()["access_token"]
    wa = TestClient(cp.app, headers={"Authorization": f"Bearer {acc_a}"})
    r = wa.post("/worker-api/v1/heartbeat", json={"worker_id": b["worker_id"]})
    assert r.status_code == 403 and r.json()["code"] == "WORKER_MISMATCH"
    assert wa.post("/worker-api/v1/lease", json={"worker_id": b["worker_id"], "wait_s": 0}).status_code == 403
    assert token(cp, a["worker_id"], b["refresh_token"], ka).status_code == 401  # B's refresh with A's id
    forged = jwt.encode({"sub": b["worker_id"], "aud": "worker", "exp": 9999999999}, "guess", algorithm="HS256")
    wf = TestClient(cp.app, headers={"Authorization": f"Bearer {forged}"})
    assert wf.post("/worker-api/v1/heartbeat", json={"worker_id": b["worker_id"]}).status_code == 401
    none_alg = jwt.encode({"sub": b["worker_id"], "aud": "worker", "exp": 9999999999}, None, algorithm="none")
    assert TestClient(cp.app, headers={"Authorization": f"Bearer {none_alg}"}).post(
        "/worker-api/v1/heartbeat", json={"worker_id": b["worker_id"]}
    ).status_code == 401
    assert TestClient(cp.app).post("/worker-api/v1/heartbeat", json={"worker_id": a["worker_id"]}).status_code == 401
