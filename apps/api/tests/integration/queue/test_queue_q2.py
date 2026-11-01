import shutil
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, insert, select

from opagent_db.schema import job, job_item, operation, result

CFG = {"task_type": "classify", "prompt_template_ref": "builtin:fused_v1", "label_set": "builtin-12@1",
       "generation_params": {"temperature": 0}}
FX = Path(__file__).resolve().parents[5] / "fixtures/photos/basic"


def ok(item):
    return {"job_item_id": item["id"], "attempt": item["attempt"], "idempotency_key": f"{item['id']}:{item['attempt']}",
            "status": "ok", "output": {"category": "food", "confidence": 0.9}, "model_fingerprint": "fp1"}


def op_status(q, job_id="j1"):
    with q.eng.begin() as c:
        return c.execute(select(operation.c.status).where(operation.c.id == job_id)).scalar_one()


@pytest.mark.integration
@pytest.mark.tc("Q-009")
def test_q_009_graceful_pause(q):
    q.seed(10)
    leased = q.lease("w1", 4)
    assert q.client.post("/api/v1/jobs/j1/pause", json={"mode": "graceful"}).status_code == 200
    assert q.lease("w1", 4) == []  # no new leases
    q.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": [ok(i) for i in leased]})
    st = q.items()
    assert sum(i["status"] == "done" for i in st.values()) == 4
    assert q.client.get("/api/v1/jobs/j1").json()["status"] == "paused"
    assert op_status(q) == "paused"
    q.client.post("/api/v1/jobs/j1/resume")
    assert len(q.lease("w1", 16)) == 6


@pytest.mark.integration
@pytest.mark.tc("Q-010")
def test_q_010_immediate_pause_returns_leases(q):
    q.seed(6)
    leased = q.lease("w1", 4)
    q.client.post("/api/v1/jobs/j1/pause", json={"mode": "immediate"})
    st = q.items()
    for i in leased:
        assert (st[i["id"]]["status"], st[i["id"]]["attempts"]) == ("pending", 0)
    hb = q.client.post("/worker-api/v1/heartbeat", json={"worker_id": "w1", "lease_ids": [i["id"] for i in leased]})
    assert sorted(hb.json()["abort"]) == sorted(i["id"] for i in leased)


@pytest.mark.chaos
@pytest.mark.tc("Q-011")
def test_q_011_restart_continues_from_db(q, sandbox_tree, tmp_path):
    from fastapi.testclient import TestClient

    from opagent_api.app import create_app
    from opagent_inference.fake import FakeBackend
    from opagent_worker.agent import WorkerAgent

    for i in range(20):
        shutil.copyfile(FX / f"IMG_{i + 1:04d}.jpg", sandbox_tree.photos / f"{i}.jpg")
    q.seed(20)
    w1 = WorkerAgent(q.client, roots=sandbox_tree.roots, home=tmp_path / "w", backend=FakeBackend(), worker_id="w1")
    w1.batch_size = 5
    w1.process_items_once()
    w1.process_items_once()
    # "restart": a fresh API process and a fresh worker process over the same database / worker home
    client2 = TestClient(create_app(dsn=q.dsn, clock=q.clk))
    w2 = WorkerAgent(client2, roots=sandbox_tree.roots, home=tmp_path / "w", backend=FakeBackend(), worker_id="w1")
    w2.batch_size = 5
    while w2.process_items_once():
        pass
    c = client2.get("/api/v1/jobs/j1").json()["counters"]
    assert c["done"] + c["cached"] + c["failed"] + c["review"] + c["skipped"] == c["total"] == 20


@pytest.mark.integration
@pytest.mark.tc("Q-012")
def test_q_012_cancel_keeps_results_and_new_job_hits_cache(q):
    from opagent_api.queue import create_label_items

    q.seed(6)
    leased = q.lease("w1", 2)
    q.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": [ok(leased[0])]})
    q.client.post("/api/v1/jobs/j1/cancel")
    assert q.client.get("/api/v1/jobs/j1").json()["status"] == "cancelling"
    q.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": [ok(leased[1])]})
    assert q.client.get("/api/v1/jobs/j1").json()["status"] == "cancelled"
    st = [i["status"] for i in q.items().values()]
    assert st.count("done") == 2 and st.count("cancelled") == 4
    with q.eng.begin() as c:
        c.execute(insert(job).values(id="j2", workspace_id="default", kind="label", status="running",
                                     params={"task_config": CFG, "fingerprint": "fp1"}))
        made = create_label_items(c, q.clk, "j2", [(f"j1-a{i}", "photos", f"{i}.jpg") for i in range(6)], CFG, "fp1")
    assert made["cached"] == 2


@pytest.mark.chaos
@pytest.mark.tc("Q-013")
def test_q_013_worker_offline_outbox(q, sandbox_tree, tmp_path):
    from opagent_inference.fake import FakeBackend
    from opagent_worker.agent import WorkerAgent

    for i in range(4):
        shutil.copyfile(FX / f"IMG_{i + 1:04d}.jpg", sandbox_tree.photos / f"{i}.jpg")
    q.seed(4)

    class Flaky:
        def __init__(self, inner):
            self.inner, self.down = inner, False

        def post(self, url, **kw):
            if self.down and url.endswith("/results"):
                raise httpx.ConnectError("cp unreachable")
            return self.inner.post(url, **kw)

    fl = Flaky(q.client)
    w = WorkerAgent(fl, roots=sandbox_tree.roots, home=tmp_path / "w", backend=FakeBackend(), worker_id="w1")
    w.batch_size = 4
    fl.down = True
    assert w.process_items_once() == 4  # batch finishes locally; results go to the outbox
    assert w.outbox_size() == 4
    assert all(i["status"] == "leased" for i in q.items().values())
    fl.down = False
    assert w.flush_outbox() == 4
    assert w.flush_outbox() == 0
    assert all(i["status"] == "done" for i in q.items().values())
    with q.eng.begin() as c:
        assert c.execute(select(func.count()).select_from(result)).scalar_one() == 4


@pytest.mark.integration
@pytest.mark.tc("Q-015")
def test_q_015_thresholds_only_reapply(q):
    q.seed(4)
    for it in q.lease("w1", 4):
        q.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": [ok(it)]})
    pf = q.client.post("/api/v1/preflight", json={"action": "job.start", "params": {
        "kind": "label", "rerun_of": "j1", "task_config": CFG, "fingerprint": "fp1"}}).json()
    assert pf["ready"] is True
    assert [w["code"] for w in pf["warnings"]] == ["JOB_NOTHING_TO_DO"]
    assert pf["warnings"][0]["fixes"][0]["id"] == "reapply_thresholds"
    before = len(q.items())
    r = q.client.post("/api/v1/jobs/j1/reapply", json={"thresholds": {"accept": 0.95, "review": 0.5}})
    assert r.status_code == 200
    assert r.json()["decisions"] == {"needs_review": 4}
    assert len(q.items()) == before  # no new job items dispatched


@pytest.mark.integration
@pytest.mark.tc("Q-016")
def test_q_016_variant_change_reruns_only_that_model(q):
    from opagent_api.queue import create_label_items

    emb_cfg = {"task_type": "embed", "model_version": "siglip2-so400m"}
    assets = [(f"j1-a{i}", "photos", f"{i}.jpg") for i in range(3)]
    q.seed(3)
    with q.eng.begin() as c:
        c.execute(job_item.delete())
        c.execute(insert(job).values(id="E", workspace_id="default", kind="label", status="running",
                                     params={"task_config": emb_cfg, "fingerprint": "fpE"}))
        create_label_items(c, q.clk, "E", assets, emb_cfg, "fpE", task="embed")
        create_label_items(c, q.clk, "j1", assets, CFG, "fp1")
    for it in q.lease("w1", 16):
        fp = "fpE" if it["task"] == "embed" else "fp1"
        q.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": [{**ok(it), "model_fingerprint": fp}]})
    with q.eng.begin() as c:
        c.execute(insert(job).values(id="E2", workspace_id="default", kind="label", status="running",
                                     params={"task_config": emb_cfg, "fingerprint": "fpE"}))
        c.execute(insert(job).values(id="V2", workspace_id="default", kind="label", status="running",
                                     params={"task_config": CFG, "fingerprint": "fp2-new-variant"}))
        e = create_label_items(c, q.clk, "E2", assets, emb_cfg, "fpE", task="embed")
        v = create_label_items(c, q.clk, "V2", assets, CFG, "fp2-new-variant")
    assert (e["cached"], v["cached"]) == (3, 0)
    assert {i["task"] for i in q.lease("w1", 16)} == {"classify"}


@pytest.mark.integration
@pytest.mark.tc("Q-017")
def test_q_017_model_affinity(q):
    q.seed(4, job_id="V", task="classify")
    q.seed(4, job_id="E", task="embed")
    got = q.lease("w1", 4, loaded_models=["embed"])
    assert {i["task"] for i in got} == {"embed"}


@pytest.mark.integration
@pytest.mark.tc("Q-018")
def test_q_018_interactive_lane_first_and_fast(q, sandbox_tree, tmp_path):
    from opagent_inference.fake import FakeBackend
    from opagent_worker.agent import WorkerAgent

    for i in range(30):
        shutil.copyfile(FX / f"IMG_{i % 20 + 1:04d}.jpg", sandbox_tree.photos / f"{i}.jpg")
    q.seed(20, job_id="j1")
    current = q.lease("w1", 16)  # a batch is in flight
    q.seed(10, job_id="P", lane="interactive")
    with q.eng.begin() as c:
        c.execute(job_item.update().where(job_item.c.job_id == "P").values(
            rel=job_item.c.rel.op("||")("")))
    fb = FakeBackend(clock=q.clk, latency_ms_per_item=100)
    w = WorkerAgent(q.client, roots=sandbox_tree.roots, home=tmp_path / "w", backend=fb, worker_id="w2")
    w.batch_size = 10
    t0 = q.clk.monotonic()
    w.process_items_once()
    assert {i["status"] for k, i in q.items("P").items()} == {"done"}
    assert q.clk.monotonic() - t0 < 20
    assert all(q.items()[i["id"]]["status"] == "leased" for i in current)  # current batch not interrupted


@pytest.mark.unit
@pytest.mark.tc("Q-019")
def test_q_019_backpressure():
    from opagent_worker.pipeline import BatchQueue

    bq = BatchQueue(max_batches=4)
    for i in range(4):
        assert bq.can_lease()
        bq.put([f"b{i}"])
    assert not bq.can_lease()
    bq.take()
    assert bq.can_lease()
