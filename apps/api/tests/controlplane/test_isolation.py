"""Cross-workspace isolation (09b §8.4 MU-SEC-003; ADR-0016: one database per workspace)."""

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization as ser
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from opagent_api.labels import drain_index
from opagent_api.multiuser import create_user
from sqlalchemy import text

PW = "correct horse battery"


def seed(engine, prefix, n, word):
    with engine.begin() as c:
        c.execute(
            text(
                "insert into source(id, workspace_id, root_alias, include, exclude) values (:s,'default','photos','[]','[]')"
            ),
            {"s": f"{prefix}-src"},
        )
        for i in range(n):
            a = f"{prefix}-a{i}"
            c.execute(
                text(
                    "insert into asset(id, workspace_id, content_hash, size_bytes, date_source, width, height, exif)"
                    " values (:a,'default',:h,1,'exif_offset',10,10,'{}')"
                ),
                {"a": a, "h": f"{prefix}h{i}"},
            )
            c.execute(
                text(
                    "insert into asset_location(id, asset_id, source_id, rel_path, mtime, missing, size)"
                    " values (:i,:a,:s,:r,now(),false,1)"
                ),
                {"i": f"{prefix}-l{i}", "a": a, "s": f"{prefix}-src", "r": f"{word}{i}.jpg"},
            )
            c.execute(
                text(
                    "insert into result(id, asset_id, task_config_hash, model_fingerprint, task_type, category,"
                    " confidence, caption, tags, raw) values (:i,:a,'c','f','classify','travel',0.9,:cap,'[]','{}')"
                ),
                {"i": f"{prefix}-r{i}", "a": a, "cap": f"a {word} photo"},
            )
            c.execute(text("insert into index_outbox(asset_id) values (:a)"), {"a": a})
        c.execute(
            text(
                "insert into job(id, workspace_id, kind, status, params) values (:j,'default','organize','done','{}')"
            ),
            {"j": f"{prefix}-job"},
        )
        c.execute(
            text("insert into output_plan(id, job_id, status, summary) values (:p, :j, 'draft', '{}')"),
            {"p": f"{prefix}-plan", "j": f"{prefix}-job"},
        )
        drain_index(c)


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-SEC-003")
def test_sec_003_cross_workspace_isolation(cp_app, login_as):
    owner = login_as("admin@example.invalid")
    r = owner.post("/api/v1/workspaces", json={"name": "Family B"})
    assert r.status_code == 201, r.text
    wb = r.json()["id"]
    st = cp_app.state.opa
    create_user(st, "alice@example.invalid", PW, role="ws_admin")  # workspace default (A)
    create_user(st, "bob@example.invalid", PW, role="ws_admin", ws=wb)
    seed(st.engine, "A", 2, "beach")
    seed(st.ws_state(wb).engine, "B", 3, "beach")

    alice, bob = login_as("alice@example.invalid"), login_as("bob@example.invalid")
    bob.headers["X-OPA-Workspace"] = wb

    # A's user: B's ids do not exist; B's workspace looks exactly like a workspace that does not exist
    for path in ("/api/v1/assets/B-a0", "/api/v1/output-plans/B-plan", "/api/v1/assets/B-a0/media?variant=t256"):
        assert alice.get(path).status_code == 404, path
    gone = alice.get("/api/v1/assets/A-a0", headers={"X-OPA-Workspace": "ws-does-not-exist"})
    other = alice.get("/api/v1/assets/A-a0", headers={"X-OPA-Workspace": wb})
    assert gone.status_code == other.status_code == 404
    assert gone.json() == other.json()
    res = alice.post("/api/v1/search", json={"q": "beach", "limit": 50}).json()
    assert res["total"] == 2 and {x["asset_id"] for x in res["items"]} == {"A-a0", "A-a1"}
    # B's user sees B, not A
    assert bob.get("/api/v1/assets/B-a0").status_code == 200
    assert bob.get("/api/v1/assets/A-a0").status_code == 404
    assert bob.post("/api/v1/search", json={"q": "beach", "limit": 50}).json()["total"] == 3
    assert bob.get("/api/v1/assets/B-a0", headers={"X-OPA-Workspace": "default"}).status_code == 404

    # MCP: a token from A cannot reach B's photos
    pat = alice.post("/api/v1/mcp/tokens", json={"name": "a", "scopes": ["photos:read"]}).json()["token"]
    m = TestClient(cp_app, base_url="https://testserver", headers={"Authorization": f"Bearer {pat}"})

    def call(name, args, ws=None):
        h = {"X-OPA-Workspace": ws} if ws else {}
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        return m.post("/mcp/v1/rpc", json=body, headers=h).json()

    assert call("get_photo", {"id": "A-a0", "thumbnail_size": 0})["result"].get("isError") is not True
    for ws in (None, wb):  # a header cannot move a token to another workspace
        out = call("get_photo", {"id": "B-a0", "thumbnail_size": 0}, ws)
        assert out["result"]["isError"] is True and "NOT_FOUND" in json.dumps(out["result"])
        found = call("search_photos", {"query": "beach"}, ws)["result"]["structuredContent"]
        assert found["total_estimate"] == 2
    # B's token lives in B's database and works there
    bpat = bob.post("/api/v1/mcp/tokens", json={"name": "b", "scopes": ["photos:read"]}).json()["token"]
    m.headers["Authorization"] = f"Bearer {bpat}"
    assert call("search_photos", {"query": "beach"})["result"]["structuredContent"]["total_estimate"] == 3

    # a worker paired to A never sees B's work, whatever it claims
    code = alice.post("/api/v1/workers/enrollment-codes", json={"name": "a-gpu"})
    assert code.status_code == 403  # ws_admin does not manage workers
    code = owner.post("/api/v1/workers/enrollment-codes", json={"name": "a-gpu"}).json()["code"]
    sk = Ed25519PrivateKey.generate()
    pub = base64.b64encode(sk.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)).decode()
    w = TestClient(cp_app, base_url="https://testserver")
    e = w.post("/worker-api/v1/enroll", json={"code": code, "public_key": pub, "name": "a-gpu"}).json()
    ts, nonce = cp_app.clock.now().isoformat(), "n1"
    sig = base64.b64encode(sk.sign(f"{e['worker_id']}|{ts}|{nonce}".encode())).decode()
    acc = w.post(
        "/worker-api/v1/token",
        json={"worker_id": e["worker_id"], "ts": ts, "nonce": nonce, "sig": sig},
        headers={"Authorization": f"Bearer {e['refresh_token']}"},
    ).json()["access_token"]
    with st.ws_state(wb).engine.begin() as c:
        c.execute(text("insert into worker_task(id, kind, payload, status) values ('t-b', 'scan', '{}', 'pending')"))
    for ws in (None, wb):
        h = {"Authorization": f"Bearer {acc}", **({"X-OPA-Workspace": ws} if ws else {})}
        r = w.post("/worker-api/v1/lease", json={"worker_id": e["worker_id"], "wait_s": 0}, headers=h)
        assert r.status_code in (200, 204), r.text
        assert "t-b" not in r.text
    with st.ws_state(wb).engine.begin() as c:
        assert c.execute(text("select status from worker_task where id='t-b'")).scalar() == "pending"
