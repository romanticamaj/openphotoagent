"""MCP-TOOL-028 (回歸): a labelling job over a source with a sub-path read ``inbox/inbox/…`` and failed every item
with DECODE_FAILED — location paths are relative to the root, not to the source's sub-path."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_api.organize import start_label_job
from opagent_db.schema import engine_for
from opagent_inference.fake import FakeBackend
from opagent_worker.agent import WorkerAgent
from sqlalchemy import text

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"


@pytest.mark.integration
@pytest.mark.tc("MCP-TOOL-028")
def test_tool_028_label_job_reads_the_right_file(embedded_pg, sandbox_tree, tmp_path):
    (sandbox_tree.photos / "inbox").mkdir()
    for i in (1, 2):
        shutil.copyfile(FX / f"IMG_000{i}.jpg", sandbox_tree.photos / f"inbox/p{i}.jpg")
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    worker = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w", backend=FakeBackend())
    worker.register()
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "inbox"}).json()
    client.post(f"/api/v1/sources/{src['id']}/scan")
    worker.run_until_idle()
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        ids = [r[0] for r in c.execute(text("select asset_id from asset_location order by rel_path"))]
    out = start_label_job(client.app.state.opa, ids, only_unlabeled=True, priority="normal", origin={"origin": "t"})
    while worker.process_items_once():
        pass
    with eng.begin() as c:
        rows = c.execute(text("select rel, status, error_code from job_item where job_id = :j"), {"j": out["job_id"]})
        got = sorted((r.rel, r.status, r.error_code) for r in rows)
    eng.dispose()
    assert got == [("inbox/p1.jpg", "done", None), ("inbox/p2.jpg", "done", None)]
