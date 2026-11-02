import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import asset_location, engine_for
from opagent_worker.agent import WorkerAgent
from sqlalchemy import select

FX = Path(__file__).resolve().parents[4] / "fixtures/photos"


@pytest.mark.integration
@pytest.mark.tc("SCAN-024")
def test_scan_024_output_target_inside_source_not_scanned(embedded_pg, tmp_path):
    lib = tmp_path / "lib"
    (lib / "sorted/2023").mkdir(parents=True)
    shutil.copyfile(FX / "basic/IMG_0001.jpg", lib / "a.jpg")
    shutil.copyfile(FX / "basic/IMG_0002.jpg", lib / "sorted/2023/old.jpg")
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    w = WorkerAgent(client, roots=[{"alias": "lib", "path": str(lib), "mode": "rw"}], home=tmp_path / "w")
    w.register()
    src = client.post("/api/v1/sources", json={"root_alias": "lib", "sub_path": ""}).json()
    body = {
        "kind": "organize",
        "source_id": src["id"],
        "preset": "by-date",
        "target": {"root_alias": "lib", "sub_path": "sorted"},
    }
    r = client.post("/api/v1/jobs", json=body)  # preflight blocks by default (PF-056)
    assert r.status_code == 409 and r.json()["blockers"][0]["code"] == "OUTPUT_TARGET_INSIDE_SOURCE"
    r = client.post("/api/v1/jobs", json={**body, "advanced": {"allow_target_inside_source": True}})  # user insists
    assert r.status_code == 201
    w.run_until_idle()
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        rels = sorted(r for (r,) in c.execute(select(asset_location.c.rel_path)))
    eng.dispose()
    assert rels == ["a.jpg"]
