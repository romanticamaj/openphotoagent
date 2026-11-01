import shutil
import subprocess
import sys
import time as _t
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import asset, engine_for, result
from opagent_inference.fake import FakeBackend
from opagent_testing.clock import FakeClock
from opagent_worker.agent import WorkerAgent
from sqlalchemy import func, select

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"
CONTENT = {"kind": "organize", "preset": "by-content-12", "target": {"root_alias": "sorted", "sub_path": ""}}


def _photos(root: Path, sub: str, n: int) -> None:
    (root / sub).mkdir(parents=True, exist_ok=True)
    for i in range(n):
        shutil.copyfile(FX / f"IMG_{i % 20 + 1:04d}.jpg", root / sub / f"{i}.jpg")
        if i >= 20:
            with (root / sub / f"{i}.jpg").open("ab") as fh:
                fh.write(bytes([i]))


def _setup(dsn, roots, home, backend=None, clock=None):
    client = TestClient(create_app(dsn=dsn, clock=clock))
    w = WorkerAgent(client, roots=roots, home=home, backend=backend or FakeBackend(), worker_id="w1")
    w.register()
    return client, w


def _drain(w):
    for _ in range(100):
        a, b = w.run_once(), w.process_items_once()
        if not a and not b:
            return


@pytest.mark.integration
@pytest.mark.tc("Q-020")
def test_q_020_same_source_conflict(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "x", 3)
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w")
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "x"}).json()
    j = client.post("/api/v1/jobs", json={**CONTENT, "source_id": src["id"]}).json()
    pf = client.post(
        "/api/v1/preflight", json={"action": "job.start", "params": {**CONTENT, "source_id": src["id"]}}
    ).json()
    assert pf["ready"] is False
    b = pf["blockers"][0]
    assert b["code"] == "JOB_CONFLICT_RUNNING" and b["params"]["job"] == j["id"]
    assert [f["id"] for f in b["fixes"]] == ["view_job", "queue_after"]


@pytest.mark.integration
@pytest.mark.tc("SCAN-006")
def test_scan_006_moved_folder_needs_zero_inference(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "album", 12)
    fb = FakeBackend()
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", fb)
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()
    j1 = client.post("/api/v1/jobs", json={**CONTENT, "source_id": src["id"]}).json()
    _drain(w)
    assert client.get(f"/api/v1/jobs/{j1['id']}").json()["status"] == "awaiting_approval"
    calls_before = len(fb.calls())
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        n_assets = c.execute(select(func.count()).select_from(asset)).scalar_one()
    (sandbox_tree.photos / "album").rename(sandbox_tree.photos / "album-2024")
    j2 = client.post("/api/v1/jobs", json={**CONTENT, "source_id": src["id"]}).json()
    _drain(w)
    j2 = client.get(f"/api/v1/jobs/{j2['id']}").json()
    with eng.begin() as c:
        assert c.execute(select(func.count()).select_from(asset)).scalar_one() == n_assets
    eng.dispose()
    assert j2["counters"]["cached"] == j2["counters"]["total"] == 12, j2
    assert len(fb.calls()) == calls_before


@pytest.mark.integration
@pytest.mark.tc("Q-024")
def test_q_024_preview_rerender_without_gpu(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "p", 30)
    fb = FakeBackend()
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", fb)
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "p"}).json()
    w.run_once()  # nothing queued yet
    pv = client.post("/api/v1/previews", json={**CONTENT, "source_id": src["id"], "k": 10, "seed": 42}).json()
    _drain(w)
    pv = client.get(f"/api/v1/previews/{pv['id']}").json()
    assert pv["status"] == "ready" and len(pv["items"]) == 10
    calls = len(fb.calls())
    t0 = _t.perf_counter()
    r = client.post(
        f"/api/v1/previews/{pv['id']}/rerender",
        json={"thresholds": {"accept": 0.95, "review": 0.5}, "path_template": "{category}/{taken_at:%Y}/{stem}{ext}"},
    ).json()
    assert (_t.perf_counter() - t0) < 0.2
    assert len(fb.calls()) == calls
    assert all(i["dst_rel"].count("/") == 2 for i in r["items"])


@pytest.mark.chaos
@pytest.mark.tc("Q-022")
def test_q_022_kill_9_worker_no_duplicate_results(embedded_pg, sandbox_tree, tmp_path, api_server):
    _photos(sandbox_tree.photos, "k", 8)
    clk = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
    app = create_app(dsn=embedded_pg.dsn, clock=clk)
    with api_server(app) as srv:
        import httpx

        client = httpx.Client(base_url=srv.url, timeout=30)
        w = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "wA", backend=FakeBackend(), worker_id="wA")
        w.register()
        src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "k"}).json()
        job = client.post("/api/v1/jobs", json={**CONTENT, "source_id": src["id"]}).json()
        w.run_once()  # scan -> label items
        roots = ",".join(f"{r['alias']}={r['path']}:{r['mode']}" for r in sandbox_tree.roots)
        p = subprocess.run(
            [
                sys.executable,
                "-m",
                "opagent_cli",
                "worker",
                "--api",
                srv.url,
                "--roots",
                roots,
                "--worker-id",
                "wB",
                "--once",
            ],
            env={
                **__import__("os").environ,
                "OPAGENT_TEST_MODE": "1",
                "OPAGENT_CRASH_AT": "worker_after_infer",
                "OPAGENT_HOME": str(tmp_path / "hB"),
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert p.returncode == 137, p.stderr[-500:]
        clk.advance(121)
        client.post("/worker-api/v1/sweep")
        w2 = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "wC", backend=FakeBackend(), worker_id="wC")
        while w2.process_items_once():
            pass
        c = client.get(f"/api/v1/jobs/{job['id']}").json()["counters"]
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as conn:
        assert conn.execute(select(func.count()).select_from(result)).scalar_one() == 8
    eng.dispose()
    assert c["done"] == c["total"] == 8
