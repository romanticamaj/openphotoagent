"""匯入 and 整理 are separate (ADR-0029): adding a folder analyses it, and files only move through a plan."""

import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import asset, engine_for, job, job_item, output_plan, result, review, worker_task
from opagent_inference.fake import FakeBackend
from opagent_testing.clock import FakeClock
from opagent_worker.agent import WorkerAgent
from sqlalchemy import func, insert, select, update

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"


@pytest.fixture
def env(embedded_pg, sandbox_tree, tmp_path):
    class E:
        clk = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
        client = TestClient(create_app(dsn=embedded_pg.dsn, clock=clk))
        eng = engine_for(embedded_pg.dsn)

    for i in range(3):
        shutil.copyfile(FX / f"IMG_{i + 1:04d}.jpg", sandbox_tree.photos / f"{i}.jpg")
    E.tree = sandbox_tree
    E.w = WorkerAgent(
        E.client,
        roots=sandbox_tree.roots,
        home=tmp_path / "w",
        backend=FakeBackend(vlm_p=0.9),
        embedder=FakeBackend(scores="high"),
        worker_id="w1",
    )
    E.w.register()
    yield E
    E.eng.dispose()


def _serve_one(env):
    """The worker answers while the request waits for it."""
    t = threading.Thread(target=env.w.run_once, kwargs={"wait_s": 3.0})
    t.start()
    return t


@pytest.mark.integration
@pytest.mark.tc("IMP-003")
def test_imp_003_preview_goes_through_the_worker(env):
    t = _serve_one(env)
    r = env.client.get("/api/v1/fs/preview", params={"root": "photos", "path": ""})
    t.join()
    assert r.status_code == 200
    assert r.json()["photos"] == 3
    assert len(r.json()["samples"]) == 3


@pytest.mark.integration
@pytest.mark.tc("IMP-003")
def test_imp_003_thumbnails_go_through_the_worker(env):
    t = _serve_one(env)
    r = env.client.get("/api/v1/fs/thumb", params={"root": "photos", "path": "0.jpg"})
    t.join()
    assert r.status_code == 200
    assert r.json()["thumbnail_b64"]


@pytest.mark.integration
@pytest.mark.tc("IMP-003")
def test_imp_003_an_unknown_root_is_refused(env):
    assert env.client.get("/api/v1/fs/preview", params={"root": "nope"}).status_code == 409
    assert env.client.get("/api/v1/fs/thumb", params={"root": "nope", "path": "a.jpg"}).status_code == 409


@pytest.mark.integration
@pytest.mark.tc("IMP-004")
def test_imp_004_a_quick_task_is_leased_past_a_running_scan(env):
    """While a scan keeps the worker busy, browsing must still answer: only_kinds is what the side thread asks for."""
    with env.eng.begin() as c:
        c.execute(insert(worker_task).values(id="scan1", kind="scan", worker_id="w1", payload={}))
        c.execute(insert(worker_task).values(id="b1", kind="browse", worker_id="w1", payload={}))
    r = env.client.post("/worker-api/v1/lease", json={"worker_id": "w1", "only_kinds": ["browse"]})
    assert r.json()["id"] == "b1"
    assert (
        env.client.post("/worker-api/v1/lease", json={"worker_id": "w1", "only_kinds": ["browse"]}).status_code == 204
    )


@pytest.mark.integration
@pytest.mark.tc("IMP-005")
def test_imp_005_analysing_a_folder_never_makes_a_plan(env):
    src = env.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()
    r = env.client.post(f"/api/v1/sources/{src['id']}/analyze")
    assert r.status_code == 202
    jid = r.json()["job_id"]
    while env.w.run_once() or env.w.process_items_once():
        pass
    with env.eng.begin() as c:
        assert c.execute(select(func.count()).select_from(asset)).scalar() == 3
        assert c.execute(select(func.count()).select_from(result)).scalar() == 3
        assert c.execute(select(job.c.status).where(job.c.id == jid)).scalar() == "completed"
        assert c.execute(select(func.count()).select_from(output_plan)).scalar() == 0
    assert list(env.tree.sorted.iterdir()) == []


@pytest.mark.integration
@pytest.mark.tc("IMP-005")
def test_imp_005_without_a_model_it_says_why_and_a_plain_scan_still_works(env, tmp_path):
    bare = WorkerAgent(env.client, roots=env.tree.roots, home=tmp_path / "bare", worker_id="w1")
    bare.register()
    src = env.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()
    r = env.client.post(f"/api/v1/sources/{src['id']}/analyze")
    assert r.status_code == 409
    assert r.json()["blockers"]
    assert env.client.post(f"/api/v1/sources/{src['id']}/scan").status_code == 202


@pytest.mark.integration
@pytest.mark.tc("IMP-005")
def test_imp_005_asking_twice_returns_the_same_job(env):
    src = env.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()
    a = env.client.post(f"/api/v1/sources/{src['id']}/analyze").json()
    b = env.client.post(f"/api/v1/sources/{src['id']}/analyze").json()
    assert a["job_id"] == b["job_id"]
    with env.eng.begin() as c:
        assert c.execute(select(func.count()).select_from(job)).scalar() == 1


@pytest.mark.integration
@pytest.mark.tc("IMP-005")
def test_imp_005_an_unknown_source_is_404(env):
    assert env.client.post("/api/v1/sources/nope/analyze").status_code == 404


@pytest.mark.integration
@pytest.mark.tc("IMP-006")
def test_imp_006_photos_appear_while_the_scan_is_still_running(env, monkeypatch):
    """With 12,000 files the library stayed empty for over an hour: entries now go out as they are hashed."""
    import opagent_worker.scanner as S

    src = env.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()
    env.client.post(f"/api/v1/sources/{src['id']}/scan")
    env.w.page_size = 1
    env.w.scan_threads = 1  # one file at a time, so "the first page went out before the last file" is exact
    pages = []
    real = env.w.client.post

    def spy(url, **kw):
        out = real(url, **kw)
        if url.endswith("/progress") and (kw.get("json") or {}).get("entries"):
            with env.eng.begin() as c:
                pages.append(c.execute(select(func.count()).select_from(asset)).scalar())
        return out

    monkeypatch.setattr(env.w.client, "post", spy)
    hashed_after = []
    one = S._scan_one

    def track(*a, **k):
        hashed_after.append(len(pages))
        return one(*a, **k)

    monkeypatch.setattr(S, "_scan_one", track)
    env.w.run_until_idle()
    assert pages == [1, 2, 3]
    assert hashed_after == [0, 1, 2]


@pytest.mark.integration
@pytest.mark.tc("IMP-007")
def test_imp_007_threads_keep_the_order_a_resume_depends_on(env):
    """Decoding runs in parallel, but a page's cursor means everything sorting before it was reported (SCAN-019)."""
    for i in range(3, 12):
        shutil.copyfile(FX / "IMG_0001.jpg", env.tree.photos / f"{i:02d}.jpg")
    src = env.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()
    env.client.post(f"/api/v1/sources/{src['id']}/scan")
    env.w.page_size = 2
    env.w.scan_threads = 4
    cursors = []
    real = env.w.client.post

    def spy(url, **kw):
        if url.endswith("/progress") and (kw.get("json") or {}).get("cursor"):
            cursors.append(kw["json"]["cursor"])
        return real(url, **kw)

    env.w.client.post = spy
    env.w.run_until_idle()
    assert cursors == sorted(cursors)
    assert len(cursors) == 6


@pytest.mark.integration
@pytest.mark.tc("IMP-024")
def test_imp_024_a_new_analysis_retires_the_old_question(env):
    """回歸 (seen on the reference machine): reviews about results that had been replaced stayed in 待確認."""
    from opagent_api import labels

    src = env.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()
    env.client.post(f"/api/v1/sources/{src['id']}/scan")
    env.w.run_until_idle()
    st = env.client.app.state.opa
    with env.eng.begin() as c:
        aid = c.execute(select(asset.c.id)).scalars().first()
        c.execute(insert(review).values(id="old", asset_id=aid, job_id="j1", result_id="r1", status="pending"))
        labels.on_result(st, c, "j2", aid, "r2", {"decision": "accepted"})
    assert env.client.get("/api/v1/reviews", params={"limit": 0}).json()["count"] == 0


@pytest.mark.integration
@pytest.mark.tc("IMP-030")
def test_imp_030_what_is_being_analysed_right_now(env):
    """回歸 (the user could not tell which folder or photo was in progress, or what the AI had just found)."""
    env.w.embedder = FakeBackend(scores="low")  # so the VLM writes a caption
    (env.tree.photos / "trip").mkdir()
    shutil.copyfile(FX / "IMG_0001.jpg", env.tree.photos / "trip" / "x.jpg")
    src = env.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "trip"}).json()
    jid = env.client.post(f"/api/v1/sources/{src['id']}/analyze").json()["job_id"]

    def status():
        return env.client.get("/api/v1/analysis/status", params={"lang": "zh-TW"}).json()

    assert status()["jobs"][0]["state"] == "queued"
    env.w.run_once()
    s = status()
    assert s["jobs"] == [
        {"job_id": jid, "folder": "photos/trip", "state": "analysing", "done": 0, "total": 1, "failed": 0}
    ]
    with env.eng.begin() as c:
        c.execute(update(job_item).where(job_item.c.job_id == jid).values(status="leased"))
    assert status()["current"][0]["rel"] == "trip/x.jpg"
    with env.eng.begin() as c:
        c.execute(update(job_item).where(job_item.c.job_id == jid).values(status="pending"))
    while env.w.run_once() or env.w.process_items_once():
        pass
    s = status()
    assert s["jobs"] == [] and s["current"] == []
    assert s["recent"][0]["rel"] == "trip/x.jpg"
    assert s["recent"][0]["caption"]
    assert s["recent"][0]["category_name"]
