"""Selections are query handles, not ID lists (10 §6, 02 §5.1 `/selections`); batch actions on them write reviews
or produce an output plan for approval (backs E2E-SEL-001/002)."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_worker.agent import WorkerAgent

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"


@pytest.fixture
def lib(embedded_pg, sandbox_tree, tmp_path):
    for f in sorted(FX.glob("*.jpg"))[:6]:
        shutil.copyfile(f, sandbox_tree.photos / f.name)
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    w = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w")
    w.register()
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
    client.post(f"/api/v1/sources/{src}/scan")
    while w.run_once():
        pass
    return client, w, sandbox_tree


@pytest.mark.integration
def test_select_all_results_is_a_query_handle(lib):
    client, _, _ = lib
    r = client.post("/api/v1/selections", json={"query": {"q": None, "filters": {"taken_from": "2023-01-01"}}})
    assert r.status_code == 201, r.text
    sel = r.json()
    assert sel["count"] == client.post("/api/v1/search", json={"filters": {"taken_from": "2023-01-01"}}).json()["total"]
    assert sel["count"] == 6 and "asset_ids" not in sel
    # an explicit range selection (Shift+click) is the other kind of handle
    ids = [a["asset_id"] for a in client.post("/api/v1/search", json={}).json()["items"]][:2]
    two = client.post("/api/v1/selections", json={"asset_ids": ids}).json()
    assert two["count"] == 2
    assert client.post("/api/v1/selections", json={}).status_code == 422


@pytest.mark.integration
def test_batch_category_and_tags_become_corrections(lib):
    client, _, _ = lib
    sel = client.post("/api/v1/selections", json={"query": {}}).json()
    r = client.post(f"/api/v1/selections/{sel['id']}/labels", json={"category": "pets", "tags": ["貓"]})
    assert r.status_code == 200 and r.json()["updated"] == 6
    ids = [a["asset_id"] for a in client.post("/api/v1/search", json={}).json()["items"]]
    for aid in ids:
        assert client.get(f"/api/v1/assets/{aid}").json()["human_label"]["category"] == "pets"


@pytest.mark.integration
def test_batch_move_to_folder_produces_a_plan_for_approval(lib):
    client, w, tree = lib
    ids = [a["asset_id"] for a in client.post("/api/v1/search", json={}).json()["items"]][:3]
    sel = client.post("/api/v1/selections", json={"asset_ids": ids}).json()
    r = client.post(
        f"/api/v1/selections/{sel['id']}/organize", json={"target": {"root_alias": "sorted", "sub_path": "picked"}}
    )
    assert r.status_code == 201, r.text
    plan = client.get(f"/api/v1/output-plans/{r.json()['plan_id']}").json()
    assert plan["status"] == "draft" and len(plan["actions"]) == 3  # nothing touched before approval (invariant 5)
    assert all(a["action"] == "copy" and a["dst_rel"].startswith("picked/") for a in plan["actions"])
    assert not (tree.sorted / "picked").exists()
    client.post(f"/api/v1/output-plans/{plan['id']}/approve", json={})
    while w.run_once():
        pass
    assert len(list((tree.sorted / "picked").iterdir())) == 3


@pytest.mark.integration
@pytest.mark.tc("COL-021")
def test_col_021_everything_but_the_photos_taken_back_out(lib):
    """Ctrl+A on the timeline (audit round 52): the whole library as a query, minus the photos un-ticked after it."""
    client, _, _ = lib
    ids = [a["asset_id"] for a in client.post("/api/v1/search", json={"limit": 10}).json()["items"]]
    r = client.post("/api/v1/selections", json={"query": {"collapse_duplicates": False}, "exclude": ids[:2]})
    assert r.status_code == 201, r.text
    assert r.json()["count"] == 4
    sid = r.json()["id"]
    alb = client.post("/api/v1/albums", json={"name": "全部"}).json()["id"]
    added = client.post(f"/api/v1/selections/{sid}/album", json={"album_id": alb}).json()["added"]
    assert len(added) == 4 and not set(ids[:2]) & set(added)
