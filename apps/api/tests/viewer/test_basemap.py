"""Offline street map over the API (10 §2.5, ADR-0037): status, one download operation at a time, and the file served
with byte ranges for the map's PMTiles reader."""

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from sqlalchemy import create_engine, text


@pytest.mark.integration
@pytest.mark.tc("MAP-015")
def test_map_015_basemap_status_download_and_ranged_file(embedded_pg, tmp_path):
    client = TestClient(create_app(dsn=embedded_pg.dsn, home=tmp_path))
    s = client.get("/api/v1/map/basemap").json()
    assert s["available"] is False and s["region"] == "taiwan" and s["operation_id"] is None
    r = client.post("/api/v1/map/basemap/download")
    assert r.status_code == 202, r.text
    op = r.json()["operation_id"]
    assert client.get("/api/v1/map/basemap").json()["operation_id"] == op
    assert client.post("/api/v1/map/basemap/download").json()["operation_id"] == op  # a second press: the same one
    eng = create_engine(embedded_pg.dsn.replace("postgresql://", "postgresql+psycopg://"))
    with eng.connect() as c:
        assert c.execute(text("select kind from worker_task where id = :i"), {"i": op}).scalar() == "basemap"
        assert c.execute(text("select type from operation where id = :i"), {"i": op}).scalar() == "map.download"
    eng.dispose()
    (tmp_path / "maps").mkdir(exist_ok=True)
    f = tmp_path / "maps" / "taiwan.pmtiles"
    f.write_bytes(b"PMTiles\x03" + bytes(range(256)) * 4)
    s = client.get("/api/v1/map/basemap").json()
    assert s["available"] is True and s["bytes"] == f.stat().st_size
    r = client.get("/api/v1/map/basemap.pmtiles", headers={"Range": "bytes=0-6"})
    assert r.status_code == 206 and r.content == b"PMTiles"


@pytest.mark.integration
@pytest.mark.tc("MAP-015")
def test_map_015_no_data_folder_no_download(embedded_pg):
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    assert client.get("/api/v1/map/basemap").json()["available"] is False
    r = client.post("/api/v1/map/basemap/download")
    assert r.status_code == 409 and r.json()["code"] == "MAP_NO_DATA_DIR"
    assert client.get("/api/v1/map/basemap.pmtiles").status_code == 404
