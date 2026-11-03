"""Remote worker channel: protocol negotiation and the WSS → long-poll fallback (02 §5.3; 09b §8.1)."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from opagent_worker.agent import WorkerAgent
from opagent_worker.remote import Channel, RemoteClient, enroll
from sqlalchemy import text


def pair(cp_app, owner, home, http=None):
    code = owner.post("/api/v1/workers/enrollment-codes", json={"name": "gpu"}).json()["code"]
    enroll("https://testserver", code, home, "gpu", http=TestClient(cp_app, base_url="https://testserver"))
    return RemoteClient(home, http=http or TestClient(cp_app, base_url="https://testserver"), clock=cp_app.clock)


def add_task(cp_app, tid, wid):
    with cp_app.state.opa.engine.begin() as c:
        c.execute(
            text(
                "insert into worker_task(id, kind, payload, status, worker_id)"
                " values (:i, 'browse', CAST(:p AS jsonb), 'pending', :w)"
            ),
            {"i": tid, "p": json.dumps({"root_alias": "photos", "path": ""}), "w": wid},
        )


def status(cp_app, tid):
    with cp_app.state.opa.engine.begin() as c:
        return c.execute(text("select status from worker_task where id = :i"), {"i": tid}).scalar()


@pytest.mark.integration
@pytest.mark.tc("MU-WORKER-005")
def test_worker_005_protocol_two_majors_behind(cp_app, login_as, sandbox_tree, tmp_path):
    owner = login_as("admin@example.invalid")
    cp_app.state.opa.protocol_version = "3.0"  # the CP serves 3.x and 2.x
    rc = pair(cp_app, owner, tmp_path / "old")
    old = WorkerAgent(rc, roots=sandbox_tree.roots, home=tmp_path / "old", worker_id=rc.worker_id)
    old.protocol_version = "1.4"
    old.register()
    assert old.welcome["status"] == "upgrade_required" and old.welcome["min_protocol"] == "2.0"
    assert old.blocked_reason == "upgrade_required"  # shown to the person running the worker
    add_task(cp_app, "t-old", rc.worker_id)
    assert old.run_once() is False and status(cp_app, "t-old") == "pending"
    # the CP does not rely on the worker behaving: lease and poll refuse too
    r = rc.post("/worker-api/v1/lease", json={"worker_id": rc.worker_id, "wait_s": 0})
    assert r.status_code == 426 and r.json()["code"] == "UPGRADE_REQUIRED"
    assert rc.get("/worker-api/v1/poll", params={"wait": 0}).status_code == 426
    assert status(cp_app, "t-old") == "pending"
    # one major behind is still served
    rc2 = pair(cp_app, owner, tmp_path / "n1")
    n1 = WorkerAgent(rc2, roots=sandbox_tree.roots, home=tmp_path / "n1", worker_id=rc2.worker_id)
    n1.protocol_version = "2.7"
    n1.register()
    assert n1.welcome["status"] == "ok" and n1.blocked_reason is None
    add_task(cp_app, "t-n1", rc2.worker_id)
    assert n1.run_once() is True and status(cp_app, "t-n1") == "done"


class Blockable:
    """The WSS dialer, behind a proxy that can drop WebSocket upgrades."""

    def __init__(self, channel):
        self.blocked, self.real = True, channel._connect

    def __call__(self, url, headers):
        if self.blocked:
            raise ConnectionRefusedError("proxy refused the upgrade")
        return self.real(url, headers)


@pytest.mark.integration
@pytest.mark.tc("MU-WORKER-004")
def test_worker_004_wss_and_long_poll_fallback(cp_app, login_as, sandbox_tree, tmp_path, api_server):
    owner = login_as("admin@example.invalid")
    with api_server(cp_app) as srv:
        rc = pair(cp_app, owner, tmp_path / "w", http=httpx.Client(base_url=srv.url, timeout=30))
        w = WorkerAgent(rc, roots=sandbox_tree.roots, home=tmp_path / "w", worker_id=rc.worker_id)
        w.register()
        w.channel = Channel(rc, ws_retry_s=5.0)
        dial = Blockable(w.channel)
        w.channel.connect = dial
        add_task(cp_app, "t1", rc.worker_id)
        assert w.run_once(wait_s=2) is True  # WSS blocked → GET /poll?wait=…
        assert w.channel.mode == "poll" and w.channel.leases == {"ws": 0, "poll": 1}
        assert status(cp_app, "t1") == "done"
        dial.blocked = False  # the proxy lets WebSockets through again
        assert w.run_once(wait_s=0) is False  # too soon to retry WSS: nothing queued, the poll comes back empty
        assert w.channel.mode == "poll"
        cp_app.clock.advance(6)  # past ws_retry_s
        add_task(cp_app, "t2", rc.worker_id)
        assert w.run_once(wait_s=5) is True
        assert w.channel.mode == "ws" and w.channel.leases["ws"] == 1  # pushed work.available, then leased
        assert w.channel.welcome["type"] == "welcome" and w.channel.welcome["status"] == "ok"
        assert status(cp_app, "t2") == "done"
        add_task(cp_app, "t3", rc.worker_id)
        assert w.run_once(wait_s=5) is True and w.channel.leases["ws"] == 2
        # revoking the worker closes the socket at the next message
        assert owner.delete(f"/api/v1/workers/{rc.worker_id}").status_code == 200
        assert w.run_once(wait_s=2) is False
        assert w.channel.mode == "poll"
        w.channel.close()
