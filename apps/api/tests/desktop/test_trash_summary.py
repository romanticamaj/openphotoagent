"""``GET /trash/summary`` (08 §5.5): what sits in ``.opagent-trash/`` before an uninstall. Answered from the plan
journal in the database — the API never reads photo folders (invariant 1)."""

from datetime import UTC, timedelta

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import asset, engine_for, job, output_action, output_plan
from opagent_testing.clock import FakeClock
from sqlalchemy import insert, text


def _seed(dsn, clk):
    now = clk.now()
    eng = engine_for(dsn)
    with eng.begin() as c:
        for i, size in enumerate([100, 200, 50, 7, 9]):
            c.execute(insert(asset).values(id=f"a{i}", workspace_id="default", content_hash=f"h{i}", size_bytes=size))
        plans = [
            ("p1", "applied", now - timedelta(days=10)),
            ("p2", "applied", now - timedelta(days=2)),
            ("p3", "undone", now - timedelta(days=20)),
        ]
        for pid, status, at in plans:
            c.execute(
                insert(job).values(id=f"j{pid}", workspace_id="default", kind="organize", status="completed", params={})
            )
            c.execute(insert(output_plan).values(id=pid, job_id=f"j{pid}", status=status, summary={}, approved_at=at))
        rows = [
            ("p1", "a0", "move", "done", "photos"),
            ("p1", "a1", "move", "done", "photos"),
            ("p2", "a2", "move", "done", "phone"),
            ("p2", "a3", "copy", "done", "phone"),  # a copy leaves nothing in the trash
            ("p3", "a4", "move", "undone", "photos"),  # restored by undo
        ]
        c.execute(
            insert(output_action),
            [
                {
                    "id": f"x{i}",
                    "plan_id": p,
                    "seq": i,
                    "asset_id": a,
                    "action": act,
                    "status": s,
                    "src_alias": root,
                    "src_rel": f"{i}.jpg",
                    "dst_alias": "sorted",
                    "dst_rel": f"{i}.jpg",
                    "src_hash": "h",
                }
                for i, (p, a, act, s, root) in enumerate(rows)
            ],
        )
    eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("TRASH-009", "DESK-029")
def test_trash_009_summary_counts_files_bytes_and_earliest_expiry(embedded_pg):
    clk = FakeClock()
    client = TestClient(create_app(dsn=embedded_pg.dsn, clock=clk))  # creates the default workspace
    _seed(embedded_pg.dsn, clk)
    r = client.get("/api/v1/trash/summary")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["files"] == 3 and s["bytes"] == 350 and s["plans"] == 2
    assert s["roots"] == ["phone", "photos"]
    assert s["retention_days"] == 30
    assert s["earliest_expiry"] == (clk.now() - timedelta(days=10) + timedelta(days=30)).isoformat()


@pytest.mark.integration
@pytest.mark.tc("TRASH-009")
def test_trash_009_empty(embedded_pg):
    s = TestClient(create_app(dsn=embedded_pg.dsn)).get("/api/v1/trash/summary").json()
    assert s == {"files": 0, "bytes": 0, "plans": 0, "roots": [], "earliest_expiry": None, "retention_days": 30}


@pytest.mark.integration
@pytest.mark.tc("TRASH-010")
def test_trash_010_expiry_is_utc_whatever_the_database_session_zone(embedded_pg):
    """回歸: the timestamp came back in the session's zone, which initdb takes from the machine, so the API's answer
    depended on where it ran (``+08:00`` on the reference machine, ``+00:00`` in CI)."""
    db = embedded_pg.dsn.rsplit("/", 1)[-1]
    eng = engine_for(embedded_pg.dsn)
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text(f"ALTER DATABASE {db} SET timezone TO 'Asia/Taipei'"))
    eng.dispose()
    clk = FakeClock()
    client = TestClient(create_app(dsn=embedded_pg.dsn, clock=clk))
    _seed(embedded_pg.dsn, clk)
    got = client.get("/api/v1/trash/summary").json()["earliest_expiry"]
    assert got == (clk.now() - timedelta(days=10) + timedelta(days=30)).astimezone(UTC).isoformat()

