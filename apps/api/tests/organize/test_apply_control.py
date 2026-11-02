"""Cancel while applying, and an approved move really moving (backs E2E-B-005/008)."""

import shutil
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_worker.agent import WorkerAgent

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"


def _setup(dsn, roots, photos, home, n=20):
    for f in sorted(FX.glob("*.jpg"))[:n]:
        shutil.copyfile(f, photos / f.name)
    client = TestClient(create_app(dsn=dsn))
    w = WorkerAgent(client, roots=roots, home=home)
    w.register()
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
    return client, w, src


def _plan(client, w, src, **extra):
    j = client.post(
        "/api/v1/jobs",
        json={"source_id": src, "preset": "by-date", "target": {"root_alias": "sorted", "sub_path": ""}, **extra},
    ).json()
    while w.run_once():
        pass
    return j["id"], client.get(f"/api/v1/jobs/{j['id']}").json()["plan_id"]


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-017")
def test_cancel_during_apply_leaves_a_partially_applied_plan_that_undoes(embedded_pg, sandbox_tree, tmp_path):
    client, w, src = _setup(embedded_pg.dsn, sandbox_tree.roots, sandbox_tree.photos, tmp_path / "w")
    jid, pid = _plan(client, w, src)
    w.apply_throttle_s = 0.05  # test hook: slow enough to press cancel mid-apply
    client.post(f"/api/v1/output-plans/{pid}/approve", json={})
    th = threading.Thread(target=w.run_once)
    th.start()
    for _ in range(200):
        if any(a["status"] == "done" for a in client.get(f"/api/v1/output-plans/{pid}").json()["actions"]) or any(
            p.is_file() for p in sandbox_tree.sorted.rglob("*")
        ):
            break
        threading.Event().wait(0.02)
    client.post(f"/api/v1/jobs/{jid}/cancel")
    th.join(20)
    plan = client.get(f"/api/v1/output-plans/{pid}").json()
    assert plan["status"] == "partially_applied"
    ops = client.get("/api/v1/operations", params={"status": "all", "type": "output.apply"}).json()
    assert [o["status"] for o in ops] == ["cancelled"]
    copied = [p for p in sandbox_tree.sorted.rglob("*") if p.is_file()]
    assert 0 < len(copied) < 20 and plan["outcome"]["done"] == len(copied)
    assert client.post(f"/api/v1/output-plans/{pid}/undo").status_code == 200
    while w.run_once():
        pass
    assert [p for p in sandbox_tree.sorted.rglob("*") if p.is_file()] == []


@pytest.mark.integration
@pytest.mark.tc("ORG-004")
def test_an_approved_move_moves(embedded_pg, sandbox_tree, tmp_path):
    client, w, src = _setup(embedded_pg.dsn, sandbox_tree.roots, sandbox_tree.photos, tmp_path / "w", n=3)
    # the photos root is read-only in the fixture; moving needs a writable source: use the sorted root as source
    for p in list(sandbox_tree.photos.glob("*.jpg")):
        shutil.move(p, sandbox_tree.sorted / p.name)
    src = client.post("/api/v1/sources", json={"root_alias": "sorted", "sub_path": ""}).json()["id"]
    _jid, pid = _plan(client, w, src, action="move", advanced={"allow_move": True})
    r = client.post(f"/api/v1/output-plans/{pid}/approve", json={"move_confirmed": True})
    assert r.status_code == 200, r.text
    while w.run_once():
        pass
    plan = client.get(f"/api/v1/output-plans/{pid}").json()
    assert plan["status"] == "applied" and plan["outcome"]["done"] == 3
    assert list(sandbox_tree.sorted.glob("*.jpg")) == []  # originals moved away (after copy + hash verify)
    placed = [p for p in sandbox_tree.sorted.rglob("*.jpg") if ".opagent-trash" not in p.parts]
    assert len(placed) == 3  # (moved originals are kept in .opagent-trash/ for undo, 04 §3.2)
