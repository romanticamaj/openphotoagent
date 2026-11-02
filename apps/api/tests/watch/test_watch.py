"""Watch folder (05 §5; 09b §6): new photos are labelled and indexed automatically; files only move after approval.

The worker owns the watchers (native events through watchdog, polling on network file systems); the API coalesces
batches into one incremental scan per source and starts labelling. Time for the debouncer is passed in, so the
quiet period is simulated; WATCH-001 also measures the real wall time of the whole loop."""

import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from opagent_inference.fake import FakeBackend
from opagent_worker.agent import WorkerAgent
from opagent_worker.watcher import WatchManager
from sqlalchemy import text

FX = sorted((Path(__file__).resolve().parents[4] / "fixtures/photos/basic").glob("*.jpg"))


class Env:
    def __init__(self, dsn, tree, tmp, mounts=None):
        self.client = TestClient(create_app(dsn=dsn), headers={"X-OPA-UI": "1"})  # watch settings are app-only
        self.worker = WorkerAgent(self.client, roots=tree.roots, home=tmp / "w", backend=FakeBackend())
        self.worker.register()
        self.now = 0.0  # the watcher's clock: events are stamped with it, ticks pass their own time
        self.watch = WatchManager(self.worker, mounts=mounts, clock=lambda: self.now)
        self.tree = tree
        self.eng = engine_for(dsn)
        src = self.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "inbox"}).json()
        self.src = src["id"]
        (tree.photos / "inbox").mkdir(exist_ok=True)

    def sql(self, q, **p):
        with self.eng.begin() as c:
            return c.execute(text(q), p)

    def put(self, n, start=0, sub="inbox"):
        for i in range(start, start + n):
            p = self.tree.photos / sub / f"new_{i:03d}.jpg"
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FX[i % len(FX)], p)
            with p.open("ab") as fh:  # distinct content
                fh.write(f"v{i}".encode())

    def settle(self, now):
        """Let the watcher see events, look at sizes at ``now`` and again an hour later (stable), and drain."""
        self.watch.pump()
        self.watch.tick(now=now)
        self.watch.tick(now=now + 3600)
        self.drain()

    def pump_until(self, n=1, timeout=3.0):
        """inotify delivers on the observer's thread: wait until at least ``n`` events arrived."""
        got = 0
        end = time.monotonic() + timeout  # noqa: TID251 - real event delivery
        while time.monotonic() < end:  # noqa: TID251
            got += self.watch.pump()
            if got >= n:
                return got
            time.sleep(0.02)  # noqa: TID251
        return got

    def drain(self):
        """Run tasks and label items until nothing is left."""
        while True:
            busy = self.worker.run_once()
            busy = bool(self.worker.process_items_once()) or busy
            if not busy:
                return

    def labelled(self):
        return self.sql(
            "select count(distinct l.asset_id) from asset_location l join source s on s.id = l.source_id"
            " join result r on r.asset_id = l.asset_id join search_doc d on d.asset_id = l.asset_id"
            " where s.id = :s and not l.missing",
            s=self.src,
        ).scalar()

    def close(self):
        self.watch.stop()
        self.eng.dispose()


@pytest.fixture
def env(embedded_pg, sandbox_tree, tmp_path):
    e = Env(embedded_pg.dsn, sandbox_tree, tmp_path)
    yield e
    e.close()


def enable(env, **kw):
    r = env.client.put(f"/api/v1/sources/{env.src}/watch", json={"enabled": True, "debounce_s": 5, **kw})
    assert r.status_code == 200, r.text
    env.drain()  # the catch-up scan of a freshly enabled watch
    env.watch.sync()
    return r.json()


@pytest.mark.integration
@pytest.mark.tc("WATCH-001")
def test_watch_001_new_photos_labelled_and_indexed(env):
    t0 = time.monotonic()  # noqa: TID251 - WATCH-001 bounds the real wall time
    enable(env)
    env.put(20)
    for _ in range(50):
        env.settle(now=1e6)
        if env.labelled() == 20:
            break
        time.sleep(0.1)  # noqa: TID251 - inotify delivers on its own thread
    assert env.labelled() == 20
    assert time.monotonic() - t0 < 120  # noqa: TID251
    hits = env.client.post("/api/v1/search", json={"q": "new_007"}).json()
    assert hits["total"] >= 1  # indexed: findable by file name at least


@pytest.mark.integration
@pytest.mark.tc("WATCH-002")
def test_watch_002_half_written_file_waits(env):
    enable(env, stable_s=10)
    p = env.tree.photos / "inbox/big.jpg"
    p.write_bytes(FX[0].read_bytes()[:1000])
    assert env.pump_until()
    env.watch.tick(now=100)  # first look: size noted
    with p.open("ab") as fh:
        fh.write(FX[0].read_bytes()[1000:])
    env.pump_until()
    env.watch.tick(now=108)  # grew since: not stable
    env.drain()
    assert env.sql("select count(*) from asset_location where rel_path = 'inbox/big.jpg'").scalar() == 0
    env.watch.tick(now=130)
    env.drain()
    assert env.sql("select count(*) from asset_location where rel_path = 'inbox/big.jpg'").scalar() == 1


@pytest.mark.integration
@pytest.mark.tc("WATCH-003")
def test_watch_003_move_rule_only_makes_a_plan(env):
    rule = {
        "preset": "by-date",
        "action": "move",
        "target": {"root_alias": "sorted", "sub_path": ""},
        "advanced": {"allow_move": True},
    }
    state = enable(env, organize=rule)
    assert state["organize"]["action"] == "move" and state["approval_policy"] == "always_manual"
    env.put(3)
    for _ in range(50):
        env.settle(now=1e6)
        n = "select count(*) from output_plan p join output_action a on a.plan_id = p.id where p.status = 'draft'"
        if env.sql(n).scalar():
            break
        time.sleep(0.1)  # noqa: TID251
    plan = env.sql("select id, status from output_plan where status = 'draft'").one()  # earlier drafts superseded
    assert env.sql("select count(*) from output_action where plan_id = :p and action = 'move'", p=plan.id).scalar() == 3
    assert env.sql("select count(*) from worker_task where kind = 'apply'").scalar() == 0
    assert len(list((env.tree.photos / "inbox").glob("new_*.jpg"))) == 3  # nothing moved
    body = {"enabled": True, "approval_policy": "auto_non_destructive", "organize": rule}
    r = env.client.put(f"/api/v1/sources/{env.src}/watch", json=body)
    assert r.status_code == 422 and r.json()["code"] == "WATCH_MOVE_NEEDS_APPROVAL"


@pytest.mark.integration
@pytest.mark.tc("WATCH-004")
def test_watch_004_files_added_while_off_are_caught_up(env):
    enable(env)
    env.client.put(f"/api/v1/sources/{env.src}/watch", json={"enabled": False})
    env.watch.sync()
    env.put(4)
    env.settle(now=1e6)
    assert env.labelled() == 0  # not watching: nothing happened
    state = enable(env)
    assert state["catch_up_task_id"]
    env.drain()
    assert env.labelled() == 4


@pytest.mark.integration
@pytest.mark.tc("WATCH-005")
def test_watch_005_burst_is_one_scan_and_one_job(env):
    enable(env)
    d = env.tree.photos / "inbox/burst"
    d.mkdir()
    for i in range(5000):
        (d / f"{i:05d}.jpg").write_bytes(b"x")
    for _ in range(30):
        env.watch.pump()
        if env.watch.pending() >= 5000:
            break
        time.sleep(0.1)  # noqa: TID251
    assert env.watch.pending() == 5000
    env.watch.tick(now=1e6)
    env.watch.tick(now=1e6 + 60)
    tasks = env.sql("select payload from worker_task where kind = 'scan' and status = 'pending'").all()
    assert len(tasks) == 1 and tasks[0].payload["watch"]["priority"] == "low"
    assert tasks[0].payload["watch"]["files"] == 5000
    # a second burst while the scan is still queued merges into it
    (d / "late.jpg").write_bytes(b"y")
    time.sleep(0.3)  # noqa: TID251
    env.watch.pump()
    env.watch.tick(now=2e6)
    env.watch.tick(now=2e6 + 60)
    assert env.sql("select count(*) from worker_task where kind = 'scan' and status = 'pending'").scalar() == 1


@pytest.mark.integration
@pytest.mark.tc("WATCH-006")
def test_watch_006_network_share_is_polled(embedded_pg, sandbox_tree, tmp_path):
    mounts = f"/dev/vda / ext4 rw 0 0\n//nas/photos {sandbox_tree.photos} cifs rw 0 0\n"
    e = Env(embedded_pg.dsn, sandbox_tree, tmp_path, mounts=mounts)
    try:
        state = enable(e, poll_interval_s=600)
        state = e.client.get(f"/api/v1/sources/{e.src}/watch").json()
        assert state["mode"] == "poll" and state["fstype"] == "cifs" and state["poll_interval_s"] == 600
        assert "網路" in state["mode_reason"] and "600" in state["mode_reason"]
        assert e.watch.observer_kind(e.src) == "poll"
        e.put(2)
        e.watch.poll(now=0)  # first pass: everything not seen before is an event
        e.watch.tick(now=1e6)
        e.watch.tick(now=1e6 + 60)
        e.drain()
        assert e.labelled() == 2
    finally:
        e.close()
