import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import asset, engine_for, job, output_action, output_plan
from sqlalchemy import insert


def _plan_with_moves(dsn: str, n_moves: int) -> str:
    eng = engine_for(dsn)
    with eng.begin() as c:
        c.execute(
            insert(job).values(id="j1", workspace_id="default", kind="organize", status="awaiting_approval", params={})
        )
        c.execute(insert(output_plan).values(id="p1", job_id="j1", status="draft", summary={"move": n_moves}))
        c.execute(insert(asset).values(id="as1", workspace_id="default", content_hash="h", size_bytes=1))
        c.execute(
            insert(output_action),
            [
                {
                    "id": f"x{i}",
                    "plan_id": "p1",
                    "seq": i,
                    "asset_id": "as1",
                    "action": "move",
                    "src_alias": "photos",
                    "src_rel": f"{i}.jpg",
                    "dst_alias": "sorted",
                    "dst_rel": f"{i}.jpg",
                    "src_hash": "h",
                }
                for i in range(n_moves)
            ],
        )
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


@pytest.mark.integration
@pytest.mark.tc("ORG-018")
def test_org_018_a_plan_can_be_discarded_and_then_never_approved(embedded_pg):
    """回歸 (audit round 57): two previews left two plans waiting forever; the plan page offered only 核准. A plan is
    discarded through the existing 「只標註不輸出」 edge (CORE-SM-005): the job completes, the plan stays a draft marked
    discarded, nothing on disk changes, and it can no longer be approved."""
    from sqlalchemy import text

    client = TestClient(create_app(dsn=embedded_pg.dsn))
    pid = _plan_with_moves(embedded_pg.dsn, 1)
    r = client.post(f"/api/v1/output-plans/{pid}/discard")
    assert r.status_code == 200, r.text
    got = client.get(f"/api/v1/output-plans/{pid}").json()
    assert got.get("discarded") is True and got["status"] == "draft"
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        assert c.execute(text("select status from job where id='j1'")).scalar() == "completed"
        assert c.execute(text("select count(*) from worker_task")).scalar() == 0  # nothing to apply was queued
    eng.dispose()
    r = client.post(f"/api/v1/output-plans/{pid}/approve", json={"move_confirmed": True})
    assert r.status_code == 409 and r.json()["code"] == "PLAN_DISCARDED"
    assert client.post(f"/api/v1/output-plans/{pid}/discard").status_code == 200  # twice: nothing more happens


@pytest.mark.integration
@pytest.mark.tc("ORG-019")
def test_org_019_only_a_plan_waiting_for_approval_can_be_discarded(embedded_pg):
    from sqlalchemy import text

    client = TestClient(create_app(dsn=embedded_pg.dsn))
    pid = _plan_with_moves(embedded_pg.dsn, 1)
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        c.execute(text("update job set status='applying' where id='j1'"))
        c.execute(text("update output_plan set status='applying' where id='p1'"))
    eng.dispose()
    r = client.post(f"/api/v1/output-plans/{pid}/discard")
    assert r.status_code == 409 and r.json()["code"] == "PLAN_NOT_DRAFT"
    assert client.post("/api/v1/output-plans/nope/discard").status_code == 404


@pytest.mark.integration
@pytest.mark.tc("ACT-016")
def test_act_016_activity_says_where_the_plan_stands(embedded_pg):
    """回歸 (audit round 58): a 依日期 preview is an organize job waiting for approval, and Activity said 「整理 完成」:
    the job's state and outcome go with its row, so the page can say 「計畫待核准」 or 「已捨棄」 instead."""
    from opagent_api.operations import ensure_operation
    from opagent_testing.clock import FakeClock

    client = TestClient(create_app(dsn=embedded_pg.dsn))
    pid = _plan_with_moves(embedded_pg.dsn, 1)
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        ensure_operation(c, FakeClock(), "j1", "job.label", {"kind": "job", "id": "j1", "job_kind": "organize"})
    eng.dispose()

    def row():
        ops = client.get("/api/v1/operations", params={"status": "all"}).json()
        return next(o for o in ops if o["id"] == "j1")["subject"]

    assert row().get("job_status") == "awaiting_approval" and not row().get("outcome")
    client.post(f"/api/v1/output-plans/{pid}/discard")
    assert row().get("job_status") == "completed" and row().get("outcome") == "discarded"
