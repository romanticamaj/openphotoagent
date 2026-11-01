import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from opagent_api.app import create_app
from opagent_db.schema import asset, engine_for, job, output_action, output_plan


def _plan_with_moves(dsn: str, n_moves: int) -> str:
    eng = engine_for(dsn)
    with eng.begin() as c:
        c.execute(insert(job).values(id="j1", workspace_id="default", kind="organize", status="awaiting_approval",
                                     params={}))
        c.execute(insert(output_plan).values(id="p1", job_id="j1", status="draft", summary={"move": n_moves}))
        c.execute(insert(asset).values(id="as1", workspace_id="default", content_hash="h", size_bytes=1))
        c.execute(insert(output_action), [
            {"id": f"x{i}", "plan_id": "p1", "seq": i, "asset_id": "as1", "action": "move", "src_alias": "photos",
             "src_rel": f"{i}.jpg", "dst_alias": "sorted", "dst_rel": f"{i}.jpg", "src_hash": "h"}
            for i in range(n_moves)])
    eng.dispose()
    return "p1"


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-012")
def test_out_exe_012_move_requires_second_confirmation(embedded_pg):
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    pid = _plan_with_moves(embedded_pg.dsn, 2)
    r = client.post(f"/api/v1/output-plans/{pid}/approve", json={})
    assert r.status_code == 409 and r.json()["code"] == "MOVE_NOT_CONFIRMED"
    r = client.post(f"/api/v1/output-plans/{pid}/approve", json={"move_confirmed": True})
    assert r.status_code == 200


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-019")
def test_out_exe_019_large_move_needs_confirm_text(embedded_pg):
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    pid = _plan_with_moves(embedded_pg.dsn, 10_001)
    r = client.post(f"/api/v1/output-plans/{pid}/approve", json={"move_confirmed": True})
    assert r.status_code == 409 and r.json()["code"] == "CONFIRM_TEXT_REQUIRED"
    assert r.json()["expected"] == "MOVE 10001"
    r = client.post(f"/api/v1/output-plans/{pid}/approve", json={"move_confirmed": True, "confirm_text": "MOVE 10001"})
    assert r.status_code == 200
