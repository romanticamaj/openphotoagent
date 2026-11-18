"""Restart recovery (06 §5.3 interrupted, invariant 9) and graceful/immediate cancel (06 §5.3, Q-009/010);
backs OP-006 and OP-012."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_inference.fake import FakeBackend
from opagent_worker.agent import WorkerAgent

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"
JOB = {"kind": "organize", "preset": "by-content-12", "target": {"root_alias": "sorted", "sub_path": ""}}


def _setup(dsn, roots, photos, home):
    for f in sorted(FX.glob("*.jpg")):
        shutil.copyfile(f, photos / f.name)
    client = TestClient(create_app(dsn=dsn))
    w = WorkerAgent(client, roots=roots, home=home, backend=FakeBackend(vlm_p=0.9), embedder=FakeBackend(scores="high"))
    w.register()
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
    j = client.post("/api/v1/jobs", json={**JOB, "source_id": src}).json()
    while w.run_once():  # scan → items queued
        pass
    # one batch leased and in flight (as the worker does before running inference)
    r = client.post(
        "/worker-api/v1/leases",
        json={"worker_id": w.worker_id, "boot_id": w.boot_id, "max_items": 16, "loaded_models": []},
    )
    assert r.status_code == 200, r.text
    return client, w, j["id"]


@pytest.mark.integration
def test_restart_marks_running_work_interrupted_and_resume_continues(embedded_pg, sandbox_tree, tmp_path):
    client, w, jid = _setup(embedded_pg.dsn, sandbox_tree.roots, sandbox_tree.photos, tmp_path / "w")
    assert client.get(f"/api/v1/jobs/{jid}").json()["counters"]["leased"] > 0
    # the app starts again: a new API and a new worker process (new boot id) under the same worker identity
    client2 = TestClient(create_app(dsn=embedded_pg.dsn))
    WorkerAgent(client2, roots=sandbox_tree.roots, home=tmp_path / "w", worker_id=w.worker_id).register()
    job = client2.get(f"/api/v1/jobs/{jid}").json()
    assert job["status"] == "paused" and job["counters"]["leased"] == 0  # nothing stays leased by a dead process
    op = client2.get(f"/api/v1/operations/{jid}").json()
    assert op["status"] == "interrupted"
    r = client2.post(f"/api/v1/operations/{jid}/resume")
    assert r.status_code == 200, r.text
    assert client2.get(f"/api/v1/jobs/{jid}").json()["status"] == "running"


@pytest.mark.integration
def test_re_registering_the_same_process_interrupts_nothing(embedded_pg, sandbox_tree, tmp_path):
    """A running worker re-registers when it loads a model; that is not a restart."""
    client, w, jid = _setup(embedded_pg.dsn, sandbox_tree.roots, sandbox_tree.photos, tmp_path / "w")
    w.register()
    job = client.get(f"/api/v1/jobs/{jid}").json()
    assert job["status"] == "running" and job["counters"]["leased"] > 0


@pytest.mark.integration
def test_immediate_cancel_asks_before_discarding_more_than_a_minute(embedded_pg, sandbox_tree, tmp_path):
    client, w, jid = _setup(embedded_pg.dsn, sandbox_tree.roots, sandbox_tree.photos, tmp_path / "w")
    # the worker measured 6 s per photo; a 16-photo batch in flight ≈ 96 s of work
    client.post(
        "/worker-api/v1/observations",
        json={"worker_id": w.worker_id, "kind": "items_per_s", "key": "vlm", "value": 1 / 6},
    )
    r = client.post(f"/api/v1/jobs/{jid}/cancel", json={"mode": "immediate"})
    assert r.status_code == 409
    d = r.json()  # problem+json: the detail fields sit at the top level
    assert d["code"] == "CANCEL_DISCARDS_WORK" and d["n"] > 0 and d["discard_s"] > 60
    r = client.post(f"/api/v1/jobs/{jid}/cancel", json={"mode": "immediate", "confirm": True})
    job = r.json()
    assert job["status"] == "cancelled" and job["counters"]["leased"] == 0


@pytest.mark.integration
def test_graceful_cancel_waits_for_the_batch(embedded_pg, sandbox_tree, tmp_path):
    client, w, jid = _setup(embedded_pg.dsn, sandbox_tree.roots, sandbox_tree.photos, tmp_path / "w")
    job = client.post(f"/api/v1/jobs/{jid}/cancel", json={"mode": "graceful"}).json()
    assert job["status"] == "cancelling" and job["counters"]["leased"] > 0


@pytest.mark.integration
@pytest.mark.tc("OP-021")
def test_op_021_a_preview_is_not_a_job_to_resume(embedded_pg, sandbox_tree, tmp_path):
    """回歸 (audit round 17): a preview cut short by a restart greeted every page as 「上次有 1 個工作被中斷 · 整理」.
    A preview's samples are thrown away with it; it is cancelled, and an interrupted job says what it was."""
    for f in sorted(FX.glob("*.jpg")):
        shutil.copyfile(f, sandbox_tree.photos / f.name)
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    w = WorkerAgent(
        client,
        roots=sandbox_tree.roots,
        home=tmp_path / "w",
        backend=FakeBackend(vlm_p=0.9),
        embedder=FakeBackend(scores="high"),
    )
    w.register()
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
    pv = client.post("/api/v1/previews", json={"source_id": src, "k": 4}).json()
    j = client.post("/api/v1/jobs", json={**JOB, "source_id": src}).json()
    while w.run_once():
        pass
    client2 = TestClient(create_app(dsn=embedded_pg.dsn))
    WorkerAgent(client2, roots=sandbox_tree.roots, home=tmp_path / "w", worker_id=w.worker_id).register()
    ops = {o["id"]: o for o in client2.get("/api/v1/operations").json()}
    assert pv["id"] not in ops or ops[pv["id"]]["status"] not in ("interrupted", "paused", "running")
    assert ops[j["id"]]["status"] == "interrupted"
    assert ops[j["id"]]["subject"]["job_kind"] == "organize" and ops[j["id"]]["subject"]["folder"]
