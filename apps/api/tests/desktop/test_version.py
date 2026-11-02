import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_core.version import VERSION


def _register(c, version, deployment="desktop"):
    r = c.post(
        "/worker-api/v1/register",
        json={
            "worker_id": "w1",
            "roots": [{"alias": "photos", "mode": "ro"}],
            "app_version": version,
            "deployment": deployment,
        },
    )
    assert r.status_code == 200, r.text


def _codes(c):
    src = c.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
    params = {
        "kind": "organize",
        "preset": "by-date",
        "source_id": src,
        "target": {"root_alias": "photos", "sub_path": ""},
    }
    return [
        b["code"]
        for b in c.post("/api/v1/preflight", json={"action": "job.start", "params": params}).json()["blockers"]
    ]


@pytest.mark.integration
@pytest.mark.tc("DESK-028")
def test_desk_028_desktop_detects_a_mismatched_worker(embedded_pg):
    c = TestClient(create_app(dsn=embedded_pg.dsn))
    assert c.get("/healthz").json()["version"] == VERSION
    _register(c, VERSION)
    assert "WORKER_VERSION_MISMATCH" not in _codes(c)
    _register(c, "9.9.9")  # someone replaced the bundled worker
    assert "WORKER_VERSION_MISMATCH" in _codes(c)
