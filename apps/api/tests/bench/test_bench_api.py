"""Mini benchmark end to end (03 §6; 09b §6): golden set → preflight → worker runs each candidate → report with
accuracy, speed, VRAM and the coverage–accuracy curve → one click applies the suggested τ without re-inference.

Candidates are FakeBackends with different behaviour; real model numbers are pending hardware (nightly GPU)."""

import io

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from opagent_inference.fake import FakeBackend
from opagent_worker.agent import WorkerAgent
from PIL import Image
from sqlalchemy import text


@pytest.fixture
def env(embedded_pg, sandbox_tree, tmp_path):
    d = sandbox_tree.photos / "golden"
    d.mkdir()
    for i in range(200):
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (i % 255, (i * 7) % 255, 90)).save(buf, "JPEG")
        (d / f"g{i:03d}.jpg").write_bytes(buf.getvalue())
    client = TestClient(create_app(dsn=embedded_pg.dsn), headers={"X-OPA-UI": "1"})
    worker = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w", backend=FakeBackend())
    worker.register()
    worker.bench_backends = {
        "steady": FakeBackend(vlm_category="cat", vlm_p=0.9, est_vram_mb=9000),
        "random": FakeBackend(seed=3, est_vram_mb=15000),
    }
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "golden"}).json()
    client.post(f"/api/v1/sources/{src['id']}/scan")
    worker.run_until_idle()
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        ids = [r[0] for r in c.execute(text("select asset_id from asset_location order by rel_path"))]
    yield client, worker, eng, ids
    eng.dispose()


def golden(client, ids, n_cat, n_dog):
    items = {a: "cat" for a in ids[:n_cat]} | {a: "dog" for a in ids[n_cat : n_cat + n_dog]}
    r = client.post("/api/v1/golden-sets", json={"name": "家裡的貓狗", "classes": ["cat", "dog"], "items": items})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def counts(eng):
    with eng.begin() as c:
        return {t: c.execute(text(f"select count(*) from {t}")).scalar() for t in ("job_item", "result", "worker_task")}


@pytest.mark.integration
@pytest.mark.tc("BENCH-001", "BENCH-003")
def test_bench_001_003_compare_two_candidates_and_apply_tau(env):
    client, worker, eng, ids = env
    gs = golden(client, ids, 150, 50)
    r = client.post(
        "/api/v1/benchmarks",
        json={"golden_set_id": gs, "candidates": ["steady", "random"], "constraints": {"vram_mb": 16000}},
    )
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    assert r.json()["operation_id"]  # a long action with ETA (invariant 9)
    worker.run_until_idle()
    rep = client.get(f"/api/v1/benchmarks/{bid}").json()
    assert rep["status"] == "completed"
    by = {c["name"]: c for c in rep["candidates"]}
    assert by["steady"]["accuracy"] == pytest.approx(0.75) and by["steady"]["failed"] == 0
    assert 0.3 < by["random"]["accuracy"] < 0.7
    for c in by.values():
        assert {"macro_f1", "per_class", "confusion", "coverage_curve", "ece"} <= set(c)
        assert c["vram_mb"] > 0 and c["latency"]["p50_ms"] >= 0 and c["items_per_s"] > 0
        assert c["coverage_curve"][0] == {"tau": 0.0, "coverage": 1.0, "accuracy": c["accuracy"]}
    assert by["steady"]["vram_mb"] == 9000 and by["random"]["vram_mb"] == 15000
    assert rep["recommended"] in by
    sug = by["steady"]["suggested"]  # steady is always 0.9 sure and right 75 %: no τ reaches 95 %
    assert sug is None
    rep_target = client.get(f"/api/v1/benchmarks/{bid}", params={"target": 0.7}).json()
    sug = {c["name"]: c for c in rep_target["candidates"]}["steady"]["suggested"]
    assert sug["accuracy"] >= 0.7 and sug["coverage"] > 0

    before = counts(eng)
    r = client.post(f"/api/v1/benchmarks/{bid}/apply", json={"candidate": "steady", "target": 0.7})
    assert r.status_code == 200, r.text
    assert r.json() == {"applied": {"accept": sug["tau"]}, "recompute": False}
    assert counts(eng) == before  # only a threshold changed: no inference, no tasks (invariant 7)
    assert client.get("/api/v1/settings/thresholds").json()["default"]["accept"] == sug["tau"]


@pytest.mark.integration
@pytest.mark.tc("BENCH-002")
def test_bench_002_too_few_labels_blocks_with_a_fix(env):
    client, worker, eng, ids = env
    gs = golden(client, ids, 20, 3)
    pf = client.post("/api/v1/preflight", json={"action": "benchmark.run", "params": {"golden_set_id": gs}}).json()
    assert pf["ready"] is False
    b = pf["blockers"][0]
    assert b["code"] == "BENCH_GOLDEN_TOO_SMALL" and b["params"] == {
        "n": 23,
        "min": 50,
        "per_class": 5,
        "short": ["dog"],
    }
    assert b["fixes"] and b["fixes"][0]["target"] == "label_golden"
    r = client.post("/api/v1/benchmarks", json={"golden_set_id": gs, "candidates": ["steady"]})
    assert r.status_code == 409 and r.json()["code"] == "PREFLIGHT_BLOCKED"  # same engine server-side (PF-073)
    assert [x["code"] for x in r.json()["blockers"]] == ["BENCH_GOLDEN_TOO_SMALL"]
    assert counts(eng)["worker_task"] == 1  # only the scan from the fixture
