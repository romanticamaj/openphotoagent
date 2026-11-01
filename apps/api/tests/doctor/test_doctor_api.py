import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from opagent_testing.clock import FakeClock
from opagent_worker.agent import WorkerAgent
from sqlalchemy import text


@pytest.fixture
def env(embedded_pg, sandbox_tree, tmp_path):
    clk = FakeClock()
    client = TestClient(create_app(dsn=embedded_pg.dsn, clock=clk))
    w = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w", worker_id="w1", hw_profile="rtx5070ti-win11", clock=clk)
    return {"client": client, "w": w, "clk": clk, "dsn": embedded_pg.dsn}


def _events(client):
    with client.stream("GET", "/api/v1/events", params={"once": 1}) as s:
        return "".join(s.iter_text())


@pytest.mark.integration
@pytest.mark.tc("DOC-010")
@pytest.mark.parametrize("holder,fix_id", [("opagent", "switch_to_running"), ("other.exe", "switch_port")])
def test_doc_010_port_in_use(env, holder, fix_id):
    env["w"].doctor_env = {"ports": {"52731": {"pid": 4242, "process": holder, "healthz_opagent": holder == "opagent"}}}
    env["w"].register()
    rs = env["client"].get("/api/v1/system/doctor", params={"worker_id": "w1"}).json()["results"]
    r = next(x for x in rs if x["check_id"] == "net.ports")
    assert r["status"] == "fail" and r["code"] == "NET_PORT_IN_USE"
    assert r["fixes"][0]["id"] == fix_id
    if holder != "opagent":
        assert r["fixes"][0]["type"] == "auto_fix"


@pytest.mark.security
@pytest.mark.tc("DOC-016")
def test_doc_016_auto_fix_whitelist(env):
    env["w"].doctor_env = {"ports": {"52731": {"pid": 1, "process": "other.exe", "healthz_opagent": False}}, "time_skew_s": 400}
    env["w"].register()
    c = env["client"]
    rs = c.get("/api/v1/system/doctor", params={"worker_id": "w1"}).json()["results"]
    # forged ids and arbitrary payloads are rejected
    assert c.post("/api/v1/system/fixes/rm -rf %2F/apply", json={}).status_code == 400
    assert c.post("/api/v1/system/fixes/switch_port/apply", json={"cmd": "format c:"}).status_code == 400
    # elevated actions are only offered as copy_command, never as auto_fix
    skew = next(x for x in rs if x["check_id"] == "time.skew")
    assert all(f["type"] != "auto_fix" for f in skew["fixes"])
    tok = next(f for x in rs if x["check_id"] == "net.ports" for f in x["fixes"] if f["type"] == "auto_fix")["token"]
    r = c.post(f"/api/v1/system/fixes/{tok}/apply", json={})
    assert r.status_code in (200, 202), r.text
    env["clk"].advance(301)  # tokens expire after 5 minutes
    assert c.post(f"/api/v1/system/fixes/{tok}/apply", json={}).status_code == 400


@pytest.mark.integration
@pytest.mark.tc("DOC-017")
def test_doc_017_fix_audit(env):
    env["w"].doctor_env = {"ports": {"52731": {"pid": 1, "process": "other.exe", "healthz_opagent": False}}}
    env["w"].register()
    c = env["client"]
    rs = c.get("/api/v1/system/doctor", params={"worker_id": "w1"}).json()["results"]
    tok = next(f for x in rs if x["check_id"] == "net.ports" for f in x["fixes"] if f["type"] == "auto_fix")["token"]
    c.post(f"/api/v1/system/fixes/{tok}/apply", json={})
    eng = engine_for(env["dsn"])
    with eng.begin() as conn:
        row = conn.execute(text("select actor, action, target, outcome from audit_log order by id desc limit 1")).one()
    eng.dispose()
    assert row.actor == "local-user" and row.action == "fix.apply" and row.target == "net.ports:switch_port"
    assert row.outcome in ("queued", "succeeded")


@pytest.mark.integration
@pytest.mark.tc("DOC-019")
def test_doc_019_cache_and_change_events(env):
    w, c = env["w"], env["client"]
    w.register()
    a = c.get("/api/v1/system/doctor", params={"worker_id": "w1"}).json()
    assert a["expires_at"] and all(r["expires_at"] for r in a["results"])
    w.doctor_env = {"time_skew_s": 400}
    w.register()  # a status changed (time.skew ok → fail)
    body = _events(c)
    assert "event: doctor.changed" in body and "time.skew" in body.split("event: doctor.changed")[1]
    pf = c.post("/api/v1/preflight", json={"action": "model.download", "params": {"variant": "x@y", "worker_id": "w1"}}).json()
    assert "doctor.changed" in pf["revalidate_on"]
    assert "TIME_SKEW" in [b["code"] for b in pf["blockers"]]
