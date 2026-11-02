"""What actually happened when a plan was applied (04 §3.5 done screen; backs E2E-B-006/007)."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_worker.agent import WorkerAgent

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"


@pytest.mark.integration
def test_applied_plan_reports_renamed_and_identical_conflicts(embedded_pg, sandbox_tree, tmp_path):
    for i in (1, 2, 3, 4):
        shutil.copyfile(FX / f"IMG_000{i}.jpg", sandbox_tree.photos / f"IMG_000{i}.jpg")
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    w = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w")
    w.register()
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
    j = client.post(
        "/api/v1/jobs", json={"source_id": src, "preset": "by-date", "target": {"root_alias": "sorted", "sub_path": ""}}
    ).json()
    while w.run_once():
        pass
    plan = client.get(f"/api/v1/output-plans/{client.get(f'/api/v1/jobs/{j["id"]}').json()['plan_id']}").json()
    dst = {a["src_rel"]: a["dst_rel"] for a in plan["actions"]}
    # the output folder already holds: two different files under planned names, one identical copy
    for name, content in (("IMG_0001.jpg", b"other photo"), ("IMG_0002.jpg", b"another")):
        p = sandbox_tree.sorted / dst[name]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    same = sandbox_tree.sorted / dst["IMG_0003.jpg"]
    same.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FX / "IMG_0003.jpg", same)
    client.post(f"/api/v1/output-plans/{plan['id']}/approve", json={})
    while w.run_once():
        pass
    done = client.get(f"/api/v1/output-plans/{plan['id']}").json()
    assert done["outcome"] == {"done": 3, "renamed": 2, "same_skipped": 1, "skipped": 0, "failed": 0}
    by = {a["src_rel"]: a for a in done["actions"]}
    assert by["IMG_0001.jpg"]["conflict"] == "exists_diff" and by["IMG_0001.jpg"]["dst_rel"].endswith(
        "IMG_0001 (1).jpg"
    )
    assert by["IMG_0003.jpg"]["conflict"] == "exists_same" and by["IMG_0003.jpg"]["status"] == "skipped"
