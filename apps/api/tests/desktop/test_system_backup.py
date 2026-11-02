"""Settings → Data: back up now, list, restore (02 §10 desktop row, 08 §2.3; PG-005/006 through the API)."""

import psycopg
import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.embedded import find_pg_bin
from opagent_testing.clock import FakeClock


def _count(dsn):
    with psycopg.connect(dsn) as c:
        return c.execute("SELECT count(*) FROM source").fetchone()[0]


@pytest.mark.integration
@pytest.mark.tc("PG-006")
def test_pg_006_backup_now_then_restore_through_the_api(embedded_pg, tmp_path):
    c = TestClient(
        create_app(dsn=embedded_pg.dsn, clock=FakeClock(), backups_dir=tmp_path / "backups", pg_bin=find_pg_bin())
    )
    c.post("/worker-api/v1/register", json={"worker_id": "w1", "roots": [{"alias": "photos", "mode": "ro"}]})
    c.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "a"})
    n0 = _count(embedded_pg.dsn)
    r = c.post("/api/v1/system/backup")
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["kind"] == "manual" and made["size_bytes"] > 0 and "/" not in made["name"]
    listed = c.get("/api/v1/system/backups").json()["items"]
    assert [b["name"] for b in listed] == [made["name"]]
    c.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "b"})
    assert _count(embedded_pg.dsn) == n0 + 1
    # a restore needs the explicit confirmation; then the database is back to the snapshot
    assert c.post("/api/v1/system/restore", json={"name": made["name"]}).status_code == 409
    r = c.post("/api/v1/system/restore", json={"name": made["name"], "confirm": True})
    assert r.status_code == 200, r.text
    assert _count(embedded_pg.dsn) == n0
    # names are opaque: nothing outside the backups folder can be named
    for bad in ["../x.dump", "/etc/passwd", "manual-..dump"]:
        assert c.post("/api/v1/system/restore", json={"name": bad, "confirm": True}).status_code in (400, 404)
