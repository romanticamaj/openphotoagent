import concurrent.futures as cf

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import func, select

from opagent_core.queue_model import COUNTED, ItemModel
from opagent_db.schema import result


def ok(item, cat="food", conf=0.9, key=None):
    return {"job_item_id": item["id"], "attempt": item["attempt"], "idempotency_key": key or f"{item['id']}:{item['attempt']}",
            "status": "ok", "output": {"category": cat, "confidence": conf}, "model_fingerprint": "fp1"}


@pytest.mark.integration
@pytest.mark.tc("Q-001")
def test_q_001_skip_locked_never_double_leases(q, api_server):
    q.seed(1000)
    seen: list[str] = []
    with api_server(q.app) as srv:
        def worker(w):
            got = []
            with httpx.Client(base_url=srv.url, timeout=30) as c:
                while True:
                    r = c.post("/worker-api/v1/leases", json={"worker_id": f"w{w}", "max_items": 16})
                    if r.status_code == 204:
                        return got
                    got += [i["id"] for i in r.json()["items"]]

        with cf.ThreadPoolExecutor(8) as ex:
            for got in ex.map(worker, range(8)):
                seen += got
    assert len(seen) == 1000 and len(set(seen)) == 1000


@pytest.mark.integration
@pytest.mark.tc("Q-002")
def test_q_002_expired_lease_reclaimed(q):
    q.seed(3)
    [a, *_] = q.lease("w1", 1)
    q.clk.advance(121)
    assert q.client.post("/worker-api/v1/sweep").json()["expired"] == 1
    it = q.items()[a["id"]]
    assert (it["status"], it["attempts"], it["lease_owner"]) == ("pending", 1, None)
    assert "opagent_lease_expired_total 1" in q.client.get("/metrics").text


@pytest.mark.integration
@pytest.mark.tc("Q-003")
def test_q_003_heartbeat_keeps_lease(q):
    q.seed(1)
    [a] = q.lease("w1", 1)
    for _ in range(10):  # 5 minutes of 30 s heartbeats
        q.clk.advance(30)
        assert q.client.post("/worker-api/v1/heartbeat", json={"worker_id": "w1", "lease_ids": [a["id"]]}).status_code == 200
        q.client.post("/worker-api/v1/sweep")
    assert q.items()[a["id"]]["status"] == "leased"


@pytest.mark.integration
@pytest.mark.tc("Q-004")
def test_q_004_idempotent_result_reporting(q):
    q.seed(1)
    [a] = q.lease("w1", 1)
    for _ in range(2):
        r = q.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": [ok(a)]})
        assert r.status_code == 200
    with q.eng.begin() as c:
        assert c.execute(select(func.count()).select_from(result)).scalar_one() == 1
    assert q.items()[a["id"]]["status"] == "done"


@pytest.mark.integration
@pytest.mark.tc("Q-005")
def test_q_005_late_result_after_reassignment(q):
    q.seed(1)
    [a] = q.lease("w1", 1)
    q.clk.advance(121)
    q.client.post("/worker-api/v1/sweep")
    [b] = q.lease("w2", 1)
    assert b["id"] == a["id"] and b["attempt"] == 2
    q.client.post("/worker-api/v1/results", json={"worker_id": "w2", "items": [ok(b)]})
    q.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": [ok(a)]})  # late
    with q.eng.begin() as c:
        assert c.execute(select(func.count()).select_from(result)).scalar_one() == 1
    counts = q.client.get("/api/v1/jobs/j1").json()["counters"]
    assert counts["done"] == 1 and counts["total"] == 1


@pytest.mark.integration
@pytest.mark.tc("Q-006")
def test_q_006_transient_backoff_10s_60s(q):
    q.seed(1)
    fail = lambda it: {**ok(it), "status": "failed", "error_code": "MODEL_OOM", "retryable": True}  # noqa: E731
    [a] = q.lease("w1", 1)
    q.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": [fail(a)]})
    assert q.lease("w1", 1) == []
    q.clk.advance(10)
    [a2] = q.lease("w1", 1)
    q.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": [fail(a2)]})
    q.clk.advance(59)
    assert q.lease("w1", 1) == []
    q.clk.advance(1)
    [a3] = q.lease("w1", 1)
    q.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": [ok(a3)]})
    it = q.items()[a["id"]]
    assert (it["status"], it["attempts"]) == ("done", 3)


@pytest.mark.integration
@pytest.mark.tc("Q-007")
def test_q_007_oom_halves_batch(q, tmp_path, sandbox_tree):
    from opagent_inference.fake import FakeBackend
    from opagent_worker.agent import WorkerAgent

    import shutil
    from pathlib import Path

    fx = Path(__file__).resolve().parents[5] / "fixtures/photos/basic"
    for i in range(16):
        shutil.copyfile(fx / f"IMG_{i + 1:04d}.jpg", sandbox_tree.photos / f"{i}.jpg")
    q.seed(16)
    fb = FakeBackend()
    fb.inject("OOM", on_batch_size_at_least=9)
    fb.inject("OOM", on_batch_size_at_least=5, times=2)  # each 8-item half OOMs once more -> 4
    w = WorkerAgent(q.client, roots=sandbox_tree.roots, home=tmp_path / "w", backend=fb, worker_id="w1")
    w.register()
    w.batch_size = 16
    w.process_items_once()
    assert [c["n"] for c in fb.calls()] == [4] * 4
    assert all(i["status"] == "done" for i in q.items().values())
    with q.eng.begin() as c:
        sizes = {r.raw["provenance"]["batch_size"] for r in c.execute(select(result))}
    assert sizes == {4}


@pytest.mark.integration
@pytest.mark.tc("Q-008")
def test_q_008_permanent_not_retried(q):
    q.seed(1)
    [a] = q.lease("w1", 1)
    q.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": [
        {**ok(a), "status": "failed", "error_code": "DECODE_FAILED", "retryable": False}]})
    q.clk.advance(3600)
    assert q.lease("w1", 1) == []
    it = q.items()[a["id"]]
    assert (it["status"], it["error_code"], it["attempts"]) == ("failed", "DECODE_FAILED", 1)


@pytest.mark.integration
@pytest.mark.tc("Q-014")
def test_q_014_cache_hit_items_never_dispatched(q):
    q.seed(5, job_id="A")
    with q.eng.begin() as c:
        c.execute(job_item.update().values(status="pending"))
    for it in q.lease("w1", 16):
        q.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": [ok(it)]})
    # job B over the same assets with the same task config + fingerprint
    from opagent_api.queue import create_label_items

    with q.eng.begin() as c:
        c.execute(insert(job).values(id="B", workspace_id="default", kind="label", status="running",
                                     params={"task_config": CFG, "fingerprint": "fp1"}))
        assets = [(f"A-a{i}", "photos", f"{i}.jpg") for i in range(5)]
        create_label_items(c, q.clk, "B", assets, CFG, "fp1")
    statuses = {i["status"] for i in q.items("B").values()}
    assert statuses == {"cached"}
    assert q.lease("w1", 16) == []
    assert q.client.get("/api/v1/jobs/B").json()["phases"]["infer"] == "skipped"


from opagent_db.schema import job, job_item  # noqa: E402
from sqlalchemy import insert  # noqa: E402

CFG = {"task_type": "classify", "prompt_template_ref": "builtin:fused_v1", "label_set": "builtin-12@1",
       "generation_params": {"temperature": 0}}

EVENTS = st.lists(st.tuples(st.sampled_from(["lease", "result", "fail", "fail_perm", "expire", "cancel", "cache"]),
                            st.integers(0, 9)), max_size=80)


@pytest.mark.property
@pytest.mark.tc("Q-021")
@given(events=EVENTS)
def test_q_021_counters_invariant(events):
    m = ItemModel.new(10)
    for ev, idx in events:
        m.apply(ev, f"i{idx}")
        counts = m.counters()
        assert sum(counts[k] for k in COUNTED) == counts["total"] == 10
