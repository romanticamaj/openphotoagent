import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from opagent_api.app import create_app
from opagent_db.schema import engine_for, journal_mirror


@pytest.mark.integration
@pytest.mark.tc("OUT-JRN-005")
def test_out_jrn_005_cp_mirror_idempotent(embedded_pg):
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    batch = {
        "worker_id": "w1",
        "entries": [
            {
                "plan_id": "p1",
                "seq": i,
                "action_id": f"a{i}",
                "op": "copy",
                "phase": "intent",
                "args": {},
                "pre_state": {},
                "post_state": None,
            }
            for i in range(1, 6)
        ],
    }
    for _ in range(2):
        r = client.post("/worker-api/v1/journal", json=batch)
        assert r.status_code == 200, r.text
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        assert c.execute(select(func.count()).select_from(journal_mirror)).scalar_one() == 5
    # the worker copy is authoritative: a re-sent entry with different content does not overwrite history
    batch["entries"][0]["op"] = "move"
    client.post("/worker-api/v1/journal", json=batch)
    with eng.begin() as c:
        assert c.execute(select(journal_mirror.c.op).where(journal_mirror.c.seq == 1)).scalar_one() == "copy"
    eng.dispose()
