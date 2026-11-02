"""What the organize screen shows while a job runs: waiting reason, source online/offline, pending reviews
(04 §3.4, 06 §5; backs E2E-B-004/009, REV-005)."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_worker.agent import WorkerAgent

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"


@pytest.mark.integration
def test_job_view_reports_an_offline_source_and_recovers(embedded_pg, sandbox_tree, tmp_path):
    (sandbox_tree.photos / "trip").mkdir()
    for f in sorted(FX.glob("*.jpg"))[:3]:
        shutil.copyfile(f, sandbox_tree.photos / "trip" / f.name)
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    w = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w")
    w.register()
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "trip"}).json()["id"]
    away = sandbox_tree.photos.with_name("photos-unplugged")
    sandbox_tree.photos.rename(away)
    j = client.post(
        "/api/v1/jobs", json={"source_id": src, "preset": "by-date", "target": {"root_alias": "sorted", "sub_path": ""}}
    ).json()
    w.run_once()
    view = client.get(f"/api/v1/jobs/{j['id']}").json()
    assert view["waiting_reason"] == "SOURCE_OFFLINE" and view["source_status"] == "offline"
    assert view["reviews_pending"] == 0
    away.rename(sandbox_tree.photos)
    w.RETRY_WAITING_S = 0
    w.tick_waiting()
    while w.run_once():
        pass
    view = client.get(f"/api/v1/jobs/{j['id']}").json()
    assert view["status"] == "awaiting_approval" and view["waiting_reason"] is None
    assert view["source_status"] == "online"
