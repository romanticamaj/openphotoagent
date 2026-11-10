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
    client, w = _setup(
        embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="random", seed=11), FakeBackend(seed=11)
    )
    j = _job(client, w, _src(client))
    assert j["status"] == "awaiting_approval", j
    plan = _plan(client, j)
    acts = [a for a in plan["actions"] if a["action"] in ("copy", "move")]
    assert len(acts) == 500
    assert len({a["asset_id"] for a in acts}) == 500
    folders = {a["dst_rel"].split("/")[0] for a in acts}
    assert folders <= {
        "人物",
        "風景",
        "美食",
        "動物",
        "文件與截圖",
        "旅行",
        "活動",
        "建築",
        "植物",
        "交通",
        "藝術",
        "其他",
    }


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
    assert r.status_code == 422 and r.json()["code"] == "MOVE_REQUIRES_ADVANCED"  # problem+json (PF-082)
    r = client.post(
        "/api/v1/jobs", json={**CONTENT, "source_id": src, "action": "move", "advanced": {"allow_move": True}}
    )
    assert r.status_code == 201


@pytest.mark.integration
@pytest.mark.tc("ORG-007")
def test_org_007_preview_estimates_distribution_and_time(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "p", 40)
    client, w = _setup(
        embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="random", seed=2), FakeBackend(seed=2)
    )
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
    client, w = _setup(
        embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), FakeBackend(vlm_p=0.9)
    )
    _job(client, w, _src(client))
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        n = c.execute(
            text("select count(*) from search_doc where tsv @@ plainto_tsquery('simple', 'photo')")
        ).scalar_one()
        pending = c.execute(text("select count(*) from index_outbox where processed_at is null")).scalar_one()
    eng.dispose()
    assert n == 6 and pending == 0


def _reviews(client, job_id, status="pending"):
    return client.get("/api/v1/reviews", params={"job_id": job_id, "status": status}).json()


@pytest.mark.integration
@pytest.mark.tc("REV-001")
def test_rev_001_low_confidence_creates_reviews(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "r", 8)
    client, w = _setup(
        embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), FakeBackend(vlm_p=0.4)
    )
    j = _job(client, w, _src(client))
    revs = _reviews(client, j["id"])
    assert revs["count"] == 8 and all(r["reason"] == "low_confidence" for r in revs["items"])
    assert all(r["suggested"]["category"] for r in revs["items"])
    with client.stream("GET", "/api/v1/events", params={"once": 1}) as s:  # no Last-Event-ID: whole outbox
        body = "".join(s.iter_text())
    assert body.count("event: review.created") == 8


@pytest.mark.integration
@pytest.mark.tc("REV-003")
def test_rev_003_skip_rest_to_other(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "r", 12)
    client, w = _setup(
        embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), FakeBackend(vlm_p=0.4)
    )
    j = _job(client, w, _src(client))
    first = _reviews(client, j["id"])["items"][0]
    client.post(f"/api/v1/reviews/{first['id']}", json={"action": "correct", "category": "pets"})
    r = client.post(f"/api/v1/jobs/{j['id']}/reviews/skip-rest").json()
    assert r["skipped"] == 11
    assert _reviews(client, j["id"])["count"] == 0
    skipped = _reviews(client, j["id"], status="skipped")["items"]
    assert len(skipped) == 11 and all(
        x["final"]["category"] == "other" and x["final"]["source"] == "review_skip" for x in skipped
    )
    j = client.get(f"/api/v1/jobs/{j['id']}").json()
    acts = _plan(client, j)["actions"]
    folders = sorted(a["dst_rel"].split("/")[0] for a in acts)
    assert folders.count("其他") == 11 and folders.count("動物") == 1


@pytest.mark.integration
@pytest.mark.tc("REV-004")
def test_rev_004_plan_without_reviewing(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "r", 5)
    client, w = _setup(
        embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), FakeBackend(vlm_p=0.4)
    )
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
    assert act["dst_rel"].startswith("動物/")
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        cat = c.execute(text("select category from search_doc where asset_id = :a"), {"a": aid}).scalar_one()
    eng.dispose()
    assert cat == "pets"


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-005")
def test_inf_cas_005_info_panel_exposes_source_and_fingerprint(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "i", 3)
    client, w = _setup(
        embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="mixed"), FakeBackend(vlm_p=0.9)
    )
    j = _job(client, w, _src(client))
    aid = _plan(client, j)["actions"][0]["asset_id"]
    info = client.get(f"/api/v1/assets/{aid}").json()
    lab = info["labels"][0]
    assert lab["source"] in ("embedding", "vlm", "consensus", "rule") and lab["model_fingerprint"]
    assert lab["decision"] in ("accepted", "needs_review", "other") and "stages" in lab


@pytest.mark.integration
@pytest.mark.tc("DESK-036")
def test_desk_036_image_payloads_carry_the_content_hash(embedded_pg, sandbox_tree, tmp_path):
    """The desktop UI reads thumbnails from the local cache by content hash (10 §5.4): every payload that
    shows a photo carries ``ch``."""
    _photos(sandbox_tree.photos, "m", 4)
    client, w = _setup(
        embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), FakeBackend(vlm_p=0.4)
    )
    src = _src(client)
    j = _job(client, w, src)
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        hashes = dict(c.execute(text("select id, content_hash from asset")).all())
    eng.dispose()
    assert len(hashes) == 4
    month = client.get("/api/v1/timeline/buckets").json()["buckets"][0]["month"]
    for a in client.get(f"/api/v1/timeline/buckets/{month}").json()["assets"]:
        assert a["ch"] == hashes[a["id"]]
    for r in _reviews(client, j["id"])["items"]:
        assert r["ch"] == hashes[r["asset_id"]]
    hits = client.post("/api/v1/search", json={"q": "photo"}).json()["items"]
    assert hits and all(h["ch"] == hashes[h["asset_id"]] for h in hits)
    pv = client.post("/api/v1/previews", json={"source_id": src, "k": 4}).json()
    _drain(w)
    items = client.get(f"/api/v1/previews/{pv['id']}").json()["items"]
    assert items and all(i["ch"] == hashes[i["asset_id"]] for i in items)


def _snapshot(root: Path) -> dict[str, tuple[str, int]]:
    import blake3

    return {
        str(p.relative_to(root)): (blake3.blake3(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.integration
@pytest.mark.nightly
@pytest.mark.tc("ORG-005")
def test_org_005_full_library_default_settings_leaves_every_original_untouched(embedded_pg, sandbox_tree, tmp_path):
    """Invariant 4 at library scale: 10,000 photos through scan → by-content → approve → apply (defaults: copy)."""
    _photos(sandbox_tree.photos, "lib", 10_000)
    before = _snapshot(sandbox_tree.photos)
    client, w = _setup(
        embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="random", seed=5), FakeBackend(seed=5)
    )
    j = client.post("/api/v1/jobs", json={**CONTENT, "source_id": _src(client)}).json()
    while w.run_once() or w.process_items_once():
        pass
    j = client.get(f"/api/v1/jobs/{j['id']}").json()
    assert j["status"] == "awaiting_approval", j
    assert client.post(f"/api/v1/output-plans/{j['plan_id']}/approve", json={}).status_code in (200, 202)
    while w.run_once() or w.process_items_once():
        pass
    plan = _plan(client, j)
    assert plan["status"] == "applied" and sum(a["status"] == "done" for a in plan["actions"]) == 10_000
    assert _snapshot(sandbox_tree.photos) == before  # content (BLAKE3) and mtime of every original unchanged


@pytest.mark.integration
@pytest.mark.tc("DESK-023")
def test_desk_023_job_waits_on_battery_and_continues_on_mains(embedded_pg, sandbox_tree, tmp_path):
    from opagent_worker.policy import FakePower, ResourcePolicy

    _photos(sandbox_tree.photos, "bat", 6)
    client, w = _setup(
        embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), FakeBackend(vlm_p=0.9)
    )
    power = FakePower(on_ac=False)
    w.policy = ResourcePolicy(power=power)
    j = client.post("/api/v1/jobs", json={**CONTENT, "source_id": _src(client)}).json()
    for _ in range(50):  # scan and planning still run; labelling must not
        if not w.run_once():
            break
    assert w.process_items_once() == 0
    op = client.get(f"/api/v1/operations/{j['id']}").json()
    assert op["status"] == "waiting" and op["waiting_reason"] == "PAUSED_ON_BATTERY"
    power.on_ac = True  # plugged back in: continues by itself
    _drain(w)
    assert client.get(f"/api/v1/jobs/{j['id']}").json()["status"] == "awaiting_approval"


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-018")
def test_inf_cas_018_a_vlm_timeout_is_retried_not_kept_as_other(embedded_pg, sandbox_tree, tmp_path):
    """回歸 (reference machine): with the GPU shared, 107 photos timed out in the VLM; each was stored as the
    embedding's guess (other, 0.2) under the normal cache key, so analysing again only brought the same answer back."""
    _photos(sandbox_tree.photos, "t", 4)
    vlm = FakeBackend(vlm_p=0.9)
    vlm.inject("TIMEOUT")
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), vlm)
    src = _src(client)
    client.post(f"/api/v1/sources/{src}/analyze")
    _drain(w)
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        assert c.execute(text("select count(*) from result")).scalar_one() == 0  # nothing to reuse
        codes = {r[0] for r in c.execute(text("select error_code from job_item"))}
    assert codes == {"RUNTIME_TIMEOUT"}
    vlm._faults.clear()  # the GPU is free again
    client.post(f"/api/v1/sources/{src}/analyze")
    _drain(w)
    with eng.begin() as c:
        cats = [r[0] for r in c.execute(text("select category from search_doc"))]
    eng.dispose()
    assert len(cats) == 4 and "other" not in cats


@pytest.mark.integration
@pytest.mark.tc("ORG-012")
def test_org_012_the_estimate_counts_what_is_already_known(embedded_pg, sandbox_tree, tmp_path):
    """回歸 (audit round 16): every sample read 「人物 100%」 while the library estimate said 「其他 73，人物 0」: the
    estimate used the embedding fast path alone, though the folder's photos already had results for this config and
    model. Those count as they are; only the rest is projected from the fast path."""
    from collections import Counter

    _photos(sandbox_tree.photos, "k", 8)
    client, w = _setup(
        embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), FakeBackend(vlm_p=0.95)
    )
    src = _src(client)
    _job(client, w, src)  # every photo analysed with the built-in categories
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        cats = Counter(r[0] for r in c.execute(text("select coalesce(category, 'other') from result")))
    eng.dispose()
    pv = client.post("/api/v1/previews", json={"source_id": src, "k": 4}).json()
    _drain(w)
    est = client.get(f"/api/v1/previews/{pv['id']}").json()["estimate"]
    assert est["basis"]["known"] == 8 and sum(est["distribution"].values()) == 8
    assert {k: v for k, v in est["distribution"].items() if v} == dict(cats)


@pytest.mark.integration
@pytest.mark.tc("ORG-013")
def test_org_013_a_running_preview_says_how_far_it_is(embedded_pg, sandbox_tree, tmp_path):
    """回歸 (audit round 16): the preview ran about 60 s with nothing on screen; its time only showed at the end."""
    _photos(sandbox_tree.photos, "q", 6)
    client, w = _setup(embedded_pg.dsn, sandbox_tree.roots, tmp_path / "w", FakeBackend(scores="low"), FakeBackend())
    pv = client.post("/api/v1/previews", json={"source_id": _src(client), "k": 4}).json()
    got = client.get(f"/api/v1/previews/{pv['id']}").json()
    assert got["status"] == "running"
    assert got["progress"]["done"] == 0 and got["progress"]["elapsed_s"] >= 0  # total is 0 while the folder is read
    _drain(w)
    got = client.get(f"/api/v1/previews/{pv['id']}").json()
    assert got["progress"]["done"] == got["progress"]["total"] == 4
