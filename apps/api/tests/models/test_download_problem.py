import threading
import time

import pytest


def _poll(f, timeout=10.0):
    t0 = time.monotonic()  # noqa: TID251 - waiting on a real worker thread
    while time.monotonic() - t0 < timeout:  # noqa: TID251
        v = f()
        if v:
            return v
        time.sleep(0.05)  # noqa: TID251
    raise AssertionError("condition not reached")


@pytest.mark.integration
@pytest.mark.tc("DL-023")
def test_unreachable_network_shows_waiting_with_fixes_and_cancels_fast(env):
    """06 §2.9 + §5: while backing off, the download Operation waits with NET_UNREACHABLE (「網路中斷，30 秒後重試」)
    and exposes the catalog problem with retry / mirrors / proxy fixes, instead of failing minutes later without a
    reason; cancelling during the backoff still ends within 2 s (backs E2E-A-004, ACT-003)."""
    c, w = env["client"], env["worker"]
    w.download_sources = ["http://127.0.0.1:9"]  # connection refused
    r = c.post("/api/v1/workers/w1/installs", json={"variant": "siglip2-so400m-384@onnx-int8"})
    assert r.status_code == 202, r.text
    op_id = r.json()["operation_id"]
    th = threading.Thread(target=w.run_once, daemon=True)
    th.start()
    op = _poll(lambda: (o := c.get(f"/api/v1/operations/{op_id}").json())["status"] == "waiting" and o)
    assert op["waiting_reason"] == "NET_UNREACHABLE"
    err = op["error"]
    assert err["code"] == "NET_UNREACHABLE" and err["title_key"] == "errors.NET_UNREACHABLE.title"
    assert [(f["type"], f.get("target")) for f in err["fixes"]] == [
        ("retry", None),
        ("open_settings", "mirrors"),
        ("open_settings", "proxy"),
    ]
    t0 = time.monotonic()  # noqa: TID251
    assert c.post(f"/api/v1/operations/{op_id}/cancel").status_code in (200, 202)
    th.join(5)
    assert not th.is_alive() and time.monotonic() - t0 < 2.5  # noqa: TID251
    assert c.get(f"/api/v1/operations/{op_id}").json()["status"] == "cancelled"
    assert not (w.models_root / ".downloads" / op_id).exists()  # .part removed (DL-005 default)


@pytest.mark.integration
@pytest.mark.tc("OP-018")
@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_op_018_pause_and_cancel_from_waiting(env, action):
    """Regression (ADR-0004): pausing/cancelling a waiting Operation used to be accepted and silently ignored."""
    from opagent_db.schema import engine_for, operation, worker_task
    from sqlalchemy import select, update

    c = env["client"]
    eng = engine_for(env["dsn"])

    def make_waiting(leased: bool) -> str:
        op_id = c.post("/api/v1/workers/w1/installs", json={"variant": "siglip2-so400m-384@onnx-int8"}).json()[
            "operation_id"
        ]
        with eng.begin() as x:
            x.execute(
                update(operation)
                .where(operation.c.id == op_id)
                .values(status="waiting", waiting_reason="NET_UNREACHABLE")
            )
            x.execute(
                update(worker_task).where(worker_task.c.id == op_id).values(status="leased" if leased else "pending")
            )
        return op_id

    def status(op_id):
        with eng.connect() as x:
            r = x.execute(select(operation.c.status, operation.c.waiting_reason).where(operation.c.id == op_id)).one()
        return r.status, r.waiting_reason

    leased = make_waiting(leased=True)
    assert c.post(f"/api/v1/operations/{leased}/{action}").status_code in (200, 202)
    assert status(leased)[0] == {"pause": "pausing", "cancel": "cancelling"}[action]  # the worker must confirm

    free = make_waiting(leased=False)
    assert c.post(f"/api/v1/operations/{free}/{action}").status_code in (200, 202)
    if action == "pause":
        assert status(free) == ("paused", "PAUSED_BY_USER")
    else:
        assert status(free)[0] == "cancelled"
    eng.dispose()


@pytest.mark.integration
def test_a_running_download_does_not_block_other_worker_tasks(env):
    """04 §8.2 W6 ∥ W7: models download in the background while the user browses folders and scans; a download
    stuck in backoff must not hold up the worker's other tasks (one download at a time, 06 §2.4)."""
    c, w = env["client"], env["worker"]
    w.download_sources = ["http://127.0.0.1:9"]
    stop = threading.Event()
    th = threading.Thread(target=w.run_forever, args=(stop,), daemon=True)
    th.start()
    try:
        a = c.post("/api/v1/workers/w1/installs", json={"variant": "siglip2-so400m-384@onnx-int8"}).json()
        _poll(lambda: c.get(f"/api/v1/operations/{a['operation_id']}").json()["status"] == "waiting")
        t0 = time.monotonic()  # noqa: TID251
        r = c.get("/api/v1/fs/browse", params={"root": "photos", "path": ""})
        assert r.status_code == 200 and time.monotonic() - t0 < 3  # noqa: TID251
        # a second download waits its turn instead of running concurrently
        b = c.post("/api/v1/workers/w1/installs", json={"variant": "siglip2-so400m-384@onnx-fp16"})
        if b.status_code == 202:
            time.sleep(1.5)  # noqa: TID251
            assert c.get(f"/api/v1/operations/{b.json()['operation_id']}").json()["status"] == "queued"
    finally:
        c.post(f"/api/v1/operations/{a['operation_id']}/cancel")
        stop.set()
        th.join(10)
