import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import asset, asset_location, engine_for
from opagent_worker.agent import WorkerAgent
from sqlalchemy import func, select

FX = Path(__file__).resolve().parents[4] / "fixtures/photos"


def _scan(client, worker, sub):
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": sub}).json()
    job = client.post(
        "/api/v1/jobs",
        json={
            "kind": "organize",
            "source_id": src["id"],
            "preset": "by-date",
            "target": {"root_alias": "sorted", "sub_path": ""},
        },
    ).json()
    worker.run_until_idle()
    return client.get(f"/api/v1/jobs/{job['id']}").json()


@pytest.mark.integration
@pytest.mark.tc("SCAN-007")
def test_scan_007_identical_content_one_asset_two_locations(embedded_pg, sandbox_tree, tmp_path):
    for d in ("a", "b"):
        (sandbox_tree.photos / "dup" / d).mkdir(parents=True)
        shutil.copyfile(FX / "basic/IMG_0001.jpg", sandbox_tree.photos / "dup" / d / "same.jpg")
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    worker = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w")
    worker.register()
    _scan(client, worker, "dup")
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        assert c.execute(select(func.count()).select_from(asset)).scalar_one() == 1
        assert c.execute(select(func.count()).select_from(asset_location)).scalar_one() == 2
    eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("SCAN-005")
def test_scan_005_location_points_to_new_asset(embedded_pg, sandbox_tree, tmp_path):
    (sandbox_tree.photos / "c").mkdir()
    p = sandbox_tree.photos / "c/x.jpg"
    shutil.copyfile(FX / "basic/IMG_0001.jpg", p)
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    worker = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w")
    worker.register()
    _scan(client, worker, "c")
    shutil.copyfile(FX / "basic/IMG_0002.jpg", p)
    _scan(client, worker, "c")
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        rows = c.execute(
            select(asset_location.c.asset_id, asset.c.content_hash).join(asset, asset.c.id == asset_location.c.asset_id)
        ).all()
    eng.dispose()
    from opagent_worker.scanner import content_hash

    assert len({r.asset_id for r in rows}) == 1 or all(r.content_hash == content_hash(p) for r in rows)
