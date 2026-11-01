import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from opagent_api.app import create_app
from opagent_db.schema import asset_location, engine_for, operation
from opagent_testing.clock import FakeClock
from opagent_worker.agent import WorkerAgent

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic/IMG_0001.jpg"


def _many(root: Path, n: int) -> None:
    data = FX.read_bytes()
    for i in range(n):
        d = root / f"d{i // 500}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{i:05d}.jpg").write_bytes(data + i.to_bytes(4, "big"))


@pytest.mark.chaos
@pytest.mark.tc("SCAN-019")
def test_scan_019_pages_and_cursor_resume(embedded_pg, sandbox_tree, tmp_path):
    _many(sandbox_tree.photos / "big", 3500)
    clk = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
    client = TestClient(create_app(dsn=embedded_pg.dsn, clock=clk))
    w = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w", worker_id="w1")
    w.register()
    w.page_size = 1000
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "big"}).json()
    job = client.post("/api/v1/jobs", json={"kind": "organize", "source_id": src["id"], "preset": "by-date",
                                            "target": {"root_alias": "sorted", "sub_path": ""}}).json()
    w.crash_after_pages = 2  # simulated kill -9 after the second page was reported
    with pytest.raises(SystemExit):
        w.run_once()
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        assert c.execute(select(func.count()).select_from(asset_location)).scalar_one() == 2000
    clk.advance(121)
    client.post("/worker-api/v1/sweep")
    w2 = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w2", worker_id="w1")
    w2.page_size = 1000
    w2.run_until_idle()
    with eng.begin() as c:
        assert c.execute(select(func.count()).select_from(asset_location)).scalar_one() == 3500
    eng.dispose()
    assert w2.last_scan_skipped_before_cursor == 2000  # resumed from the cursor, nothing re-reported
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "awaiting_approval"


@pytest.mark.chaos
@pytest.mark.tc("SCAN-020")
def test_scan_020_source_offline_waits_then_resumes(embedded_pg, sandbox_tree, tmp_path):
    _many(sandbox_tree.photos / "ext", 30)
    clk = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
    client = TestClient(create_app(dsn=embedded_pg.dsn, clock=clk))
    w = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w", worker_id="w1", clock=clk)
    w.register()
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "ext"}).json()
    job = client.post("/api/v1/jobs", json={"kind": "organize", "source_id": src["id"], "preset": "by-date",
                                            "target": {"root_alias": "sorted", "sub_path": ""}}).json()
    sandbox_tree.photos.rename(sandbox_tree.base / "unplugged")
    assert w.run_once() is True  # task leased; the source is gone -> waiting, task kept
    ops = client.get("/api/v1/operations?status=active").json()
    scan_op = next(o for o in ops if o["type"] == "scan")
    assert (scan_op["status"], scan_op["waiting_reason"]) == ("waiting", "SOURCE_OFFLINE")
    (sandbox_tree.base / "unplugged").rename(sandbox_tree.photos)
    clk.advance(30)
    w.tick_waiting()  # the worker re-checks waiting tasks; within 30 s it continues on its own
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "awaiting_approval"
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        st = c.execute(select(operation.c.status).where(operation.c.id == scan_op["id"])).scalar_one()
    eng.dispose()
    assert st == "succeeded"
