import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from opagent_inference.fake import FakeBackend
from opagent_worker.agent import WorkerAgent
from sqlalchemy import text

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"
CONTENT = {"kind": "organize", "preset": "by-content-12", "target": {"root_alias": "sorted", "sub_path": ""}}


def _photos(root: Path, sub: str, n: int) -> None:
    (root / sub).mkdir(parents=True, exist_ok=True)
    for i in range(n):
        dst = root / sub / f"{i}.jpg"
        shutil.copyfile(FX / f"IMG_{i % 20 + 1:04d}.jpg", dst)
        if i >= 20:
            with dst.open("ab") as fh:
                fh.write(i.to_bytes(4, "big"))


def _setup(dsn, roots, home, emb=None, vlm=None):
    client = TestClient(create_app(dsn=dsn))
    w = WorkerAgent(client, roots=roots, home=home, backend=vlm or FakeBackend(), embedder=emb, worker_id="w1")
    w.register()
    return client, w


def _drain(w):
    for _ in range(400):
        a, b = w.run_once(), w.process_items_once()
        if not a and not b:
            return


def _job(client, w, src, **extra):
    j = client.post("/api/v1/jobs", json={**CONTENT, "source_id": src, **extra}).json()
    _drain(w)
    return client.get(f"/api/v1/jobs/{j['id']}").json()


def _src(client, sub=""):
    return client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": sub}).json()["id"]


def _plan(client, job):
    return client.get(f"/api/v1/output-plans/{job['plan_id']}").json()


@pytest.mark.integration
@pytest.mark.tc("ORG-002")
def test_org_002_every_photo_exactly_one_destination(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "lib", 500)
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="random", seed=11), FakeBackend(seed=11))
    j = _job(client, w, _src(client))
    assert j["status"] == "awaiting_approval", j
    plan = _plan(client, j)
    acts = [a for a in plan["actions"] if a["action"] in ("copy", "move")]
    assert len(acts) == 500
    assert len({a["asset_id"] for a in acts}) == 500
    folders = {a["dst_rel"].split("/")[0] for a in acts}
    assert folders <= {"人物", "風景", "美食", "寵物", "文件與截圖", "旅行", "活動", "建築", "植物", "交通", "藝術", "其他"}


@pytest.mark.unit
@pytest.mark.tc("ORG-004")
def test_org_004_default_copy_and_move_needs_opt_in(embedded_pg, sandbox_tree, tmp_path):
    from opagent_api.organize import preset
    from opagent_api.routes_public import JobIn

    assert all(r.action == "copy" for r in preset("by-content-12").output_rules)
    assert all(r.action == "copy" for r in preset("by-date").output_rules)
    assert JobIn(source_id="s", target={"root_alias": "sorted"}).action == "copy"
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w")
    src = _src(client)
    r = client.post("/api/v1/jobs", json={**CONTENT, "source_id": src, "action": "move"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "MOVE_REQUIRES_ADVANCED"
    r = client.post("/api/v1/jobs", json={**CONTENT, "source_id": src, "action": "move", "advanced": {"allow_move": True}})
    assert r.status_code == 201


@pytest.mark.integration
@pytest.mark.tc("ORG-007")
def test_org_007_preview_estimates_distribution_and_time(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "p", 40)
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="random", seed=2), FakeBackend(seed=2))
    pv = client.post("/api/v1/previews", json={"source_id": _src(client), "k": 10}).json()
    _drain(w)
    got = client.get(f"/api/v1/previews/{pv['id']}").json()
    est = got["estimate"]
    assert est["total"] == 40 and sum(est["distribution"].values()) == 40
    assert est["basis"]["sample"] == 10 and est["basis"]["method"] == "embedding_fast_path"
    assert est["time"]["p50_s"] > 0 and est["time"]["basis"] in ("prior", "observed")


@pytest.mark.integration
@pytest.mark.tc("ORG-009")
def test_org_009_caption_reaches_search_doc(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "s", 6)
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), FakeBackend(vlm_p=0.9))
    _job(client, w, _src(client))
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        n = c.execute(text("select count(*) from search_doc where tsv @@ plainto_tsquery('simple', 'photo')")).scalar_one()
        pending = c.execute(text("select count(*) from index_outbox where processed_at is null")).scalar_one()
    eng.dispose()
    assert n == 6 and pending == 0


def _reviews(client, job_id, status="pending"):
    return client.get("/api/v1/reviews", params={"job_id": job_id, "status": status}).json()


@pytest.mark.integration
@pytest.mark.tc("REV-001")
def test_rev_001_low_confidence_creates_reviews(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "r", 8)
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), FakeBackend(vlm_p=0.4))
    j = _job(client, w, _src(client))
    revs = _reviews(client, j["id"])
    assert revs["count"] == 8 and all(r["reason"] == "low_confidence" for r in revs["items"])
    assert all(r["suggested"]["category"] for r in revs["items"])
    with client.stream("GET", "/api/v1/events", headers={"Last-Event-ID": "0"}, params={"once": 1}) as s:
        body = "".join(s.iter_text())
    assert body.count("event: review.created") == 8


@pytest.mark.integration
@pytest.mark.tc("REV-003")
def test_rev_003_skip_rest_to_other(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "r", 12)
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), FakeBackend(vlm_p=0.4))
    j = _job(client, w, _src(client))
    first = _reviews(client, j["id"])["items"][0]
    client.post(f"/api/v1/reviews/{first['id']}", json={"action": "correct", "category": "pets"})
    r = client.post(f"/api/v1/jobs/{j['id']}/reviews/skip-rest").json()
    assert r["skipped"] == 11
    assert _reviews(client, j["id"])["count"] == 0
    skipped = _reviews(client, j["id"], status="skipped")["items"]
    assert len(skipped) == 11 and all(x["final"]["category"] == "other" and x["final"]["source"] == "review_skip" for x in skipped)
    j = client.get(f"/api/v1/jobs/{j['id']}").json()
    acts = _plan(client, j)["actions"]
    folders = sorted(a["dst_rel"].split("/")[0] for a in acts)
    assert folders.count("其他") == 11 and folders.count("寵物") == 1


@pytest.mark.integration
@pytest.mark.tc("REV-004")
def test_rev_004_plan_without_reviewing(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "r", 5)
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), FakeBackend(vlm_p=0.4))
    j = _job(client, w, _src(client))
    assert _reviews(client, j["id"])["count"] == 5
    assert j["status"] == "awaiting_approval"
    acts = _plan(client, j)["actions"]
    assert all(a["dst_rel"].startswith("其他/") for a in acts)
    r = client.post(f"/api/v1/output-plans/{j['plan_id']}/approve", json={})
    assert r.status_code in (200, 202), r.text


@pytest.mark.integration
@pytest.mark.tc("ORG-010")
def test_org_010_inline_correction_feeds_back(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "c", 4)
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="high"), FakeBackend())
    src = _src(client)
    j1 = _job(client, w, src)
    aid = _plan(client, j1)["actions"][0]["asset_id"]
    r = client.patch(f"/api/v1/assets/{aid}/labels", json={"category": "pets"})
    assert r.status_code == 200 and r.json()["review"]["status"] == "corrected"
    j2 = _job(client, w, src)
    act = next(a for a in _plan(client, j2)["actions"] if a["asset_id"] == aid)
    assert act["dst_rel"].startswith("寵物/")
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        cat = c.execute(text("select category from search_doc where asset_id = :a"), {"a": aid}).scalar_one()
    eng.dispose()
    assert cat == "pets"


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-005")
def test_inf_cas_005_info_panel_exposes_source_and_fingerprint(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "i", 3)
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="mixed"), FakeBackend(vlm_p=0.9))
    j = _job(client, w, _src(client))
    aid = _plan(client, j)["actions"][0]["asset_id"]
    info = client.get(f"/api/v1/assets/{aid}").json()
    lab = info["labels"][0]
    assert lab["source"] in ("embedding", "vlm", "consensus", "rule") and lab["model_fingerprint"]
    assert lab["decision"] in ("accepted", "needs_review", "other") and "stages" in lab
