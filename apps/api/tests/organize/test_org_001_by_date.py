"""ORG-001: 依日期免模型 — preflight ready, plan -> approve -> done, no inference calls (09a §10)."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_inference.fake import FakeBackend
from opagent_worker.agent import WorkerAgent

ROOT = Path(__file__).resolve().parents[4]


def _fifty_photos(dst: Path) -> None:
    srcs = sorted((ROOT / "fixtures/photos/basic").glob("*.jpg")) + sorted(
        (ROOT / "fixtures/photos/no-exif").glob("*.jpg")
    )
    for i in range(50):
        s = srcs[i % len(srcs)]
        p = dst / f"batch{i // 25}" / f"{s.stem}_{i:02d}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(s, p)
        if i >= len(srcs):  # make duplicates distinct content (trailing bytes are ignored by JPEG decoders)
            with p.open("ab") as fh:
                fh.write(f"variant-{i}".encode())


@pytest.mark.integration
@pytest.mark.tc("ORG-001")
def test_org_001_by_date_needs_no_model(embedded_pg, sandbox_tree, tmp_path):
    _fifty_photos(sandbox_tree.photos / "trip")
    backend = FakeBackend()
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    worker = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "worker", backend=backend)
    worker.register()

    r = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "trip"})
    assert r.status_code == 201, r.text
    src = r.json()
    req = {
        "kind": "organize",
        "source_id": src["id"],
        "preset": "by-date",
        "target": {"root_alias": "sorted", "sub_path": ""},
    }
    pf = client.post("/api/v1/preflight", json={"action": "job.start", "params": req}).json()
    assert pf["ready"] is True and pf["blockers"] == []

    job = client.post("/api/v1/jobs", json=req).json()
    assert job["status"] == "queued"
    worker.run_until_idle()
    job = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert job["status"] == "awaiting_approval"
    plan = client.get(f"/api/v1/output-plans/{job['plan_id']}").json()
    assert plan["summary"]["copy"] == 50 and len(plan["actions"]) == 50

    r = client.post(f"/api/v1/output-plans/{plan['id']}/approve", json={})
    assert r.status_code == 200, r.text
    worker.run_until_idle()
    job = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert job["status"] == "completed"
    out = sorted(p.relative_to(sandbox_tree.sorted).as_posix() for p in sandbox_tree.sorted.rglob("*.jpg"))
    assert len(out) == 50
    assert all(len(p.split("/")) == 3 for p in out)  # YYYY/YYYY-MM/name
    # no inference: the backend was never loaded nor called
    assert backend.load_count == 0 and backend.calls() == []
