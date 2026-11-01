from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from opagent_api.app import create_app
from opagent_db.schema import asset, engine_for, job, job_item
from opagent_testing.clock import FakeClock

CFG = {"task_type": "classify", "prompt_template_ref": "builtin:fused_v1", "label_set": "builtin-12@1",
       "generation_params": {"temperature": 0}}


class Q:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.clk = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
        self.app = create_app(dsn=dsn, clock=self.clk)
        self.client = TestClient(self.app)
        self.eng = engine_for(dsn)

    def seed(self, n: int, job_id: str = "j1", status: str = "running", task: str = "classify",
             lane: str = "normal") -> list[str]:
        with self.eng.begin() as c:
            if not c.execute(job.select().where(job.c.id == job_id)).first():
                c.execute(insert(job).values(id=job_id, workspace_id="default", kind="label", status=status,
                                             params={"task_config": CFG, "fingerprint": "fp1"}))
            rows_a, rows_i = [], []
            for i in range(n):
                aid = f"{job_id}-a{i}"
                rows_a.append({"id": aid, "workspace_id": "default", "content_hash": f"{job_id}-h{i}", "size_bytes": 1})
                rows_i.append({"id": f"{job_id}-i{i}", "job_id": job_id, "asset_id": aid, "root_alias": "photos",
                               "rel": f"{i}.jpg", "task": task, "lane": lane, "seq": i})
            c.execute(insert(asset), rows_a)
            c.execute(insert(job_item), rows_i)
        return [r["id"] for r in rows_i]

    def lease(self, worker: str, n: int = 16, **kw) -> list[dict]:
        r = self.client.post("/worker-api/v1/leases", json={"worker_id": worker, "max_items": n, **kw})
        return [] if r.status_code == 204 else r.json()["items"]

    def items(self, job_id: str = "j1") -> dict[str, dict]:
        with self.eng.begin() as c:
            return {r.id: dict(r._mapping) for r in c.execute(job_item.select().where(job_item.c.job_id == job_id))}


@pytest.fixture
def q(embedded_pg):
    qq = Q(embedded_pg.dsn)
    yield qq
    qq.eng.dispose()
