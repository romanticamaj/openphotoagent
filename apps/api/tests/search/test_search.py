import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from opagent_inference.fake import FakeBackend
from opagent_worker.agent import WorkerAgent
from sqlalchemy import text

GROUPS = ["dog", "beach", "cat", "food", "city", "mountain", "snow", "night"]


class S:
    def __init__(self, dsn, roots, tmp):
        self.dsn = dsn
        self.client = TestClient(create_app(dsn=dsn))
        self.eng = engine_for(dsn)
        self.fb = FakeBackend(vectors="semantic")
        self.w = WorkerAgent(
            self.client, roots=roots, home=tmp / "w", backend=self.fb, embedder=self.fb, worker_id="w1"
        )
        self.w.register()
        self.src = self.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
        self.sql(
            "insert into embedding_space(model_key, fingerprint, dim) values ('fake', :f, 16)",
            f=self.fb.describe().model_fingerprint,
        )

    def sql(self, q, **p):
        with self.eng.begin() as c:
            return c.execute(text(q), p)

    def add(
        self, i, caption="", tags=(), category=None, taken=None, date_source="exif", folder="", groups=(), vec=True
    ):
        aid = f"a{i:05d}"
        taken = taken or datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=i % 700)
        self.sql(
            "insert into asset(id, workspace_id, content_hash, size_bytes, taken_at, date_source) values (:a,'default',:h,1,:t,:d)",
            a=aid,
            h=f"h{i}",
            t=taken,
            d=date_source,
        )
        rel = f"{folder}/{i}.jpg" if folder else f"{i}.jpg"
        self.sql(
            "insert into asset_location(id, asset_id, source_id, rel_path, mtime, missing) values (:i,:a,:s,:r,:t,false)",
            i=f"l{i}",
            a=aid,
            s=self.src,
            r=rel,
            t=taken,
        )
        self.sql(
            "insert into result(id, asset_id, task_config_hash, model_fingerprint, task_type, category, confidence, caption, tags, raw) values (:i,:a,'c','f','classify',:cat,0.95,:cap,:tags,'{}')",
            i=f"r{i}",
            a=aid,
            cat=category,
            cap=caption,
            tags=json.dumps(list(tags)),
        )
        self.sql("insert into index_outbox(asset_id) values (:a)", a=aid)
        if vec:
            v = self.fb.semantic_vector(list(groups))
            self.sql(
                "insert into asset_embedding(asset_id, space_id, vec, source_hash) values (:a,(select id from embedding_space limit 1),:v,:h)",
                a=aid,
                v=str(v),
                h=f"h{i}",
            )
        return aid

    def index(self):
        from opagent_api.labels import drain_index

        with self.eng.begin() as c:
            drain_index(c)

    def search(self, **body):
        r = self.client.post("/api/v1/search", json=body)
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def s(embedded_pg, sandbox_tree, tmp_path):
    x = S(embedded_pg.dsn, sandbox_tree.roots, tmp_path)
    yield x
    x.eng.dispose()


def serve(w):
    stop = threading.Event()
    t = threading.Thread(target=w.run_forever, args=(stop,), daemon=True)
    t.start()
    return stop


@pytest.mark.integration
@pytest.mark.tc("SRCH-TOK-002")
def test_srch_tok_002_simplified_query_hits_traditional(s):
    aid = s.add(1, caption="海邊的貓")
    s.add(2, caption="城市夜景")
    s.index()
    assert [x["asset_id"] for x in s.search(q="猫 海边", semantic=False)["items"]] == [aid]


@pytest.mark.integration
@pytest.mark.tc("SRCH-TOK-005")
def test_srch_tok_005_english_stemming(s):
    aid = s.add(1, caption="a cat")
    s.index()
    assert [x["asset_id"] for x in s.search(q="cats", semantic=False)["items"]] == [aid]


@pytest.mark.integration
@pytest.mark.tc("SRCH-TOK-004")
def test_srch_tok_004_bigram_downgrade(s):
    aid = s.add(
        1, caption="海灘上的夕陽"
    )  # word AND on 「夕陽海灘」 misses (different segmentation); bigrams still hit
    s.index()
    res = s.search(q="夕陽海灘", semantic=False)
    assert [x["asset_id"] for x in res["items"]] == [aid] and res["fulltext_mode"] in ("words", "bigram")


@pytest.mark.integration
@pytest.mark.tc("SRCH-FAC-001")
def test_srch_fac_001_keyword_tag_date_vs_reference(s):
    import random

    rnd = random.Random(7)
    for i in range(2000):
        cap = rnd.choice(["海邊的狗", "海邊散步", "城市夜景", "山上的雪", "一碗拉麵"])
        tags = rnd.sample(["海灘", "狗", "夜景", "雪", "拉麵"], 2)
        s.add(i, caption=cap, tags=tags, category=rnd.choice(["travel", "food", "pets"]), vec=False)
    s.index()
    res = s.search(
        q="海邊",
        filters={"tags_all": ["animal.dog"], "taken_from": "2025-01-01", "taken_to": "2026-01-01"},
        semantic=False,
        limit=500,
        facets=["category", "year"],
    )
    # reference: the filters, and the keyword through full text (R3) or its tag key (R4: 海邊 → scene.beach), 05 §2.1
    match = (
        "from search_doc where tags @> array['animal.dog'] and taken_at >= '2025-01-01' and taken_at < '2026-01-01' "
        "and (tsv @@ '''海邊'''::tsquery or tags && array['scene.beach'])"
    )
    ref = {r[0] for r in s.sql("select asset_id " + match)}
    assert {x["asset_id"] for x in res["items"]} == ref and ref
    counts = {r[0]: r[1] for r in s.sql("select category, count(*) " + match + " group by category")}
    assert res["facets"]["category"] == counts
    assert res["facets"]["year"] == {"2025": len(ref)}


@pytest.mark.integration
@pytest.mark.tc("SRCH-FAC-002")
def test_srch_fac_002_date_source_facet(s):
    s.add(1, caption="x", date_source="exif")
    b = s.add(2, caption="x", date_source="mtime")
    s.index()
    res = s.search(filters={"date_source": "mtime"}, facets=["date_source"], semantic=False)
    assert [x["asset_id"] for x in res["items"]] == [b]
    assert s.search(facets=["date_source"], semantic=False)["facets"]["date_source"] == {"exif": 1, "mtime": 1}


@pytest.mark.security
@pytest.mark.tc("SRCH-FAC-003")
def test_srch_fac_003_facets_respect_scope(s):
    for i in range(5):
        s.add(i, caption="x", category="food", folder="2024/japan")
    for i in range(5, 12):
        s.add(i, caption="x", category="pets", folder="2023")
    s.index()
    res = s.search(filters={"scope": [{"source_id": s.src, "path": "2024/japan"}]}, facets=["category"], semantic=False)
    assert res["facets"]["category"] == {"food": 5} and len(res["items"]) == 5


@pytest.mark.security
@pytest.mark.tc("SRCH-FAC-004")
def test_srch_fac_004_signed_cursor(s):
    for i in range(30):
        s.add(i, caption="x")
    s.index()
    p1 = s.search(limit=10, semantic=False)
    cur = p1["next_cursor"]
    bad = cur[:-2] + ("A" if cur[-2] != "A" else "B") + cur[-1]
    r = s.client.post("/api/v1/search", json={"limit": 10, "semantic": False, "cursor": bad})
    assert r.status_code == 400 and r.json()["code"] == "CURSOR_INVALID"
    s.add(99, caption="x", taken=datetime(2030, 1, 1, tzinfo=UTC))  # new photo would sort first
    s.index()
    seen = [x["asset_id"] for x in p1["items"]]
    while cur:
        page = s.search(limit=10, semantic=False, cursor=cur)
        seen += [x["asset_id"] for x in page["items"]]
        cur = page["next_cursor"]
    assert len(seen) == len(set(seen)) == 30  # no duplicates, nothing skipped, snapshot excludes the newcomer


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-001")
def test_srch_sem_001_text_to_image(s):
    targets = [s.add(i, groups=["dog", "beach"]) for i in range(3)]
    for i in range(3, 40):
        s.add(i, groups=[GROUPS[i % 8], GROUPS[(i + 3) % 8]] if GROUPS[i % 8] != "dog" else ["cat"])
    s.index()
    stop = serve(s.w)
    try:
        res = s.search(q="a dog on the beach", limit=5)
    finally:
        stop.set()
    assert set(targets) <= {x["asset_id"] for x in res["items"]}
    assert "image_vector" in res["items"][0]["matched_by"]


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-002")
def test_srch_sem_002_worker_offline_degrades(s):
    aid = s.add(1, caption="海邊的狗", groups=["dog", "beach"])
    s.index()
    res = s.search(q="海邊")  # no worker loop running: the query-vector RPC cannot be answered
    assert [x["asset_id"] for x in res["items"]] == [aid]
    assert res["notice"] == "degraded_search" and "image_vector" in res["degraded"]


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-003")
def test_srch_sem_003_index_progress(s):
    for i in range(30):
        s.add(i, caption="x", vec=i < 12)
    s.index()
    st = s.client.get("/api/v1/search-index").json()
    assert st["searchable"] == 12 and st["total"] == 30
    pf = s.client.post("/api/v1/preflight", json={"action": "search", "params": {}}).json()
    w = next(x for x in pf["warnings"] if x["code"] == "SEARCH_INDEX_INCOMPLETE")
    assert w["params"]["n"] == 18 and w["fixes"][0]["type"] == "auto_fix" and w["fixes"][0]["id"] == "reindex"


@pytest.mark.integration
@pytest.mark.tc("PF-053")
def test_pf_053_index_incomplete(s):
    for i in range(10):
        s.add(i, caption="x", vec=i < 8)
    s.index()
    pf = s.client.post("/api/v1/preflight", json={"action": "search", "params": {}}).json()
    assert [x["code"] for x in pf["warnings"]] == ["SEARCH_INDEX_INCOMPLETE"] and pf["ready"] is True


@pytest.mark.integration
@pytest.mark.tc("PF-054")
def test_pf_054_model_mismatch(s):
    s.add(1, caption="x")
    s.index()
    s.sql("update embedding_space set fingerprint = 'old-model'")
    pf = s.client.post("/api/v1/preflight", json={"action": "search", "params": {}}).json()
    w = next(x for x in pf["warnings"] if x["code"] == "SEARCH_MODEL_MISMATCH")
    assert w["fixes"][0]["id"] == "reindex"


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-004")
def test_srch_sem_004_selective_filter_returns_enough(s):
    for i in range(400):
        s.add(i, caption="x", groups=["dog"] if i % 8 == 0 else ["city"], category="pets" if i < 50 else "travel")
    s.index()
    stop = serve(s.w)
    try:
        res = s.search(q="dog", filters={"category": "pets"}, limit=24)
    finally:
        stop.set()
    assert len(res["items"]) == 24 and all(x["category"] == "pets" for x in res["items"])


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-005")
def test_srch_sem_005_semantic_with_date_facet(s):
    for i in range(60):
        s.add(i, groups=["beach"], taken=datetime(2023 + i % 3, 6, 1, tzinfo=UTC))
    s.index()
    stop = serve(s.w)
    try:
        res = s.search(
            q="beach", filters={"taken_from": "2024-01-01", "taken_to": "2025-01-01"}, limit=50, facets=["year"]
        )
    finally:
        stop.set()
    assert res["items"] and all(x["taken_at"].startswith("2024") for x in res["items"])
    assert res["facets"]["year"] == {"2024": 20}


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-006")
def test_srch_sem_006_review_correction_reindexed(s):
    aid = s.add(1, caption="x", tags=["狗"], category="pets")
    s.index()
    assert s.search(filters={"tags_all": ["animal.cat"]}, semantic=False)["items"] == []
    r = s.client.patch(f"/api/v1/assets/{aid}/labels", json={"category": "food", "tags": ["貓"]})
    assert r.status_code == 200
    assert [x["asset_id"] for x in s.search(filters={"tags_all": ["animal.cat"]}, semantic=False)["items"]] == [aid]


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-003")
def test_srch_sem_003_pipeline_stores_vectors_and_reindex_fills_gaps(embedded_pg, sandbox_tree, tmp_path):
    import shutil
    from pathlib import Path

    fx = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"
    (sandbox_tree.photos / "p").mkdir()
    for i in range(6):
        shutil.copyfile(fx / f"IMG_{i + 1:04d}.jpg", sandbox_tree.photos / "p" / f"{i}.jpg")
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    emb = FakeBackend(scores="high")
    w = WorkerAgent(
        client,
        roots=sandbox_tree.roots,
        home=tmp_path / "w",
        backend=FakeBackend(vlm_p=0.9),
        embedder=emb,
        worker_id="w1",
    )
    w.register()
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
    client.post(
        "/api/v1/jobs",
        json={"kind": "organize", "preset": "by-content-12", "source_id": src, "target": {"root_alias": "sorted"}},
    )
    for _ in range(100):
        if not (w.run_once() | bool(w.process_items_once())):
            break
    st = client.get("/api/v1/search-index").json()
    assert st["total"] == 6 and st["searchable"] == 6 and st["space"]["fingerprint"] == emb.describe().model_fingerprint
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        c.execute(text("delete from asset_embedding where asset_id in (select asset_id from search_doc limit 2)"))
    eng.dispose()
    assert client.get("/api/v1/search-index").json()["searchable"] == 4
    r = client.post("/api/v1/search-index/reindex")
    assert r.status_code == 202
    for _ in range(20):
        if not w.run_once():
            break
    assert client.get("/api/v1/search-index").json()["searchable"] == 6


@pytest.mark.integration
@pytest.mark.tc("SRCH-FAC-005")
def test_srch_fac_005_total_facets_and_selection_cover_every_match(s):
    """Regression (ADR-0005): more than 200 keyword matches — total, facets and "select all" are the whole match set,
    not the 200-per-route ranking window; paging has no gaps or duplicates across the window boundary."""
    for i in range(330):
        s.add(i, caption="beach day", tags=["beach"], vec=False, taken=datetime(2023 + i % 2, 6, 1, tzinfo=UTC))
    for i in range(330, 360):
        s.add(i, caption="mountain", tags=["hike"], vec=False)
    s.index()
    ref = s.sql("select count(*) from search_doc where tsv @@ to_tsquery('simple', 'beach')").scalar_one()
    assert ref == 330
    r = s.search(q="beach", semantic=False, facets=["year", "tags"], limit=100)
    assert r["total"] == 330
    assert sum(r["facets"]["year"].values()) == 330 and max(r["facets"]["tags"].values()) == 330  # canonical key
    seen, cur = [], None
    while True:
        page = s.search(q="beach", semantic=False, limit=100, cursor=cur)
        seen += [x["asset_id"] for x in page["items"]]
        cur = page["next_cursor"]
        if not cur:
            break
    assert len(seen) == 330 and len(set(seen)) == 330
    sel = s.client.post("/api/v1/selections", json={"query": {"q": "beach", "semantic": False}}).json()
    assert sel["count"] == 330
    upd = s.client.post(f"/api/v1/selections/{sel['id']}/labels", json={"tags": ["海邊"]}).json()
    assert upd["updated"] == 330


@pytest.mark.integration
@pytest.mark.tc("SRCH-RRF-003")
def test_rrf_003_filter_only_query_sorts_by_date_without_ranking(s):
    for i, day in enumerate([1, 15, 28, 40, 3]):  # 2024-03-02, -16, -29, 04-10 (outside), 03-04
        s.add(i, caption=f"photo {i}", taken=datetime(2024, 3, 1, 12, tzinfo=UTC) + timedelta(days=day))
    s.index()
    r = s.search(q="2024 年 3 月的照片", plan=True)
    assert r["planner"]["filters"]["taken_at"] == {"from": "2024-03-01", "to": "2024-04-01"}
    assert r["planner"]["text"] == ""
    ids = [h["asset_id"] for h in r["items"]]
    taken = [h["taken_at"] for h in r["items"]]
    assert len(ids) == 4 and taken == sorted(taken, reverse=True)  # taken_at desc
    assert all(h["matched_by"] == ["filter"] for h in r["items"])  # no RRF, no vector route
    assert "image_vector" not in r.get("degraded", [])


@pytest.mark.integration
@pytest.mark.tc("SRCH-RRF-004")
def test_rrf_004_duplicate_group_folds_into_its_representative(s):
    ids = [s.add(i, caption="beach sunset", tags=["scene.beach"]) for i in range(6)]
    s.index()
    s.sql(
        "insert into duplicate_member(group_id, asset_id, is_representative) values"
        " ('g', :a, true), ('g', :b, false), ('g', :c, false)",
        a=ids[0],
        b=ids[1],
        c=ids[2],
    )
    folded = s.search(q="beach")
    got = [h["asset_id"] for h in folded["items"]]
    assert ids[0] in got and ids[1] not in got and ids[2] not in got
    rep = next(h for h in folded["items"] if h["asset_id"] == ids[0])
    assert rep["duplicates"] == 2  # shown as "+2"
    assert folded["total"] == 4
    full = s.search(q="beach", collapse_duplicates=False)
    assert {h["asset_id"] for h in full["items"]} >= set(ids[:3]) and full["total"] == 6


@pytest.mark.integration
@pytest.mark.tc("SRCH-RRF-005", "SRCH-RRF-006")
def test_rrf_005_006_matched_by_names_only_routes_that_hit_and_routes_switch_off(s):
    dog = s.add(1, caption="狗在海邊", tags=["animal.dog"], groups=["dog", "beach"])
    s.add(2, caption="山上的雲", groups=["mountain"])
    s.index()
    offline = s.search(q="狗", limit=10)  # before any worker runs: R1 off, the rest unchanged
    assert "image_vector" in offline["degraded"]
    assert all("image_vector" not in h["matched_by"] for h in offline["items"])
    assert [h["asset_id"] for h in offline["items"]] == [dog]
    stop = serve(s.w)
    try:
        on = s.search(q="狗", limit=10)
        none = s.search(q="zzqv", limit=10)  # maps to no tag: R4 off; nothing in full text either
    finally:
        stop.set()
    allowed = {"image_vector", "fulltext", "tag"}
    for h in on["items"]:
        assert set(h["matched_by"]) <= allowed
    d = next(h for h in on["items"] if h["asset_id"] == dog)
    assert {"fulltext", "tag"} <= set(d["matched_by"])
    assert all("tag" not in h["matched_by"] and "fulltext" not in h["matched_by"] for h in none["items"])
    no_text = s.search(filters={"tags_any": ["animal.dog"]})  # no text: R3 / R1 off, filter only
    assert [h["matched_by"] for h in no_text["items"]] == [["filter"]]


@pytest.mark.integration
@pytest.mark.tc("SRCH-RRF-007")
def test_rrf_007_scope_is_a_pre_filter_everywhere(s):
    for i in range(1000):
        s.add(i, caption="sunset beach", tags=["scene.beach"], category="travel", folder="secret", groups=["beach"])
    inside = [
        s.add(2000 + i, caption="sunset beach", tags=["scene.beach"], category="food", folder="ok") for i in range(3)
    ]
    s.index()
    scope = {"scope": [{"source_id": s.src, "path": "ok"}]}
    stop = serve(s.w)
    try:
        r = s.search(q="sunset beach", filters=scope, facets=["category"], limit=500)
    finally:
        stop.set()
    assert sorted(h["asset_id"] for h in r["items"]) == sorted(inside)
    assert r["total"] == 3 and r["next_cursor"] is None
    assert r["facets"]["category"] == {"food": 3}  # counts never reveal the 1,000 outside the scope


def _files(*roots):
    out = set()
    for r in roots:
        out |= {p for p in Path(r).rglob("*") if p.is_file()}
    return out


@pytest.mark.integration
@pytest.mark.tc("SRCH-IMG-002")
def test_img_002_search_by_uploaded_image_keeps_it_in_memory(s, tmp_path):
    import blake3

    data = (Path(__file__).resolve().parents[4] / "fixtures/photos/basic/IMG_0003.jpg").read_bytes()
    target = s.add(1, caption="x")
    for i in range(2, 8):
        s.add(i, caption="y")
    s.index()
    space = s.sql("select id from embedding_space order by id desc limit 1").scalar_one()
    s.sql(
        "update asset_embedding set vec = :v where asset_id = :a and space_id = :s",
        v=str(s.fb.embed_vector(blake3.blake3(data).hexdigest())),
        a=target,
        s=space,
    )
    before = _files(tmp_path)
    stop = threading.Event()
    loop = threading.Thread(target=s.w.run_forever, args=(stop,), daemon=True)
    loop.start()
    try:
        r = s.client.post("/api/v1/search/by-image", files={"file": ("q.jpg", data, "image/jpeg")}, data={"k": "5"})
    finally:
        stop.set()
        loop.join(30)  # the loop closing its WAL outbox removes -shm/-wal, which raced the read below (CI, c3ac250)
    assert not loop.is_alive()
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items[0]["asset_id"] == target and items[0]["matched_by"] == ["image_vector"]
    new = _files(tmp_path) - before
    # only the worker loop's own SQLite bookkeeping may appear — no image, no blob store, no cache entry
    assert all(p.name.endswith((".db", ".db-wal", ".db-shm")) for p in new), new
    assert not any(data[:4096] in p.read_bytes() for p in _files(tmp_path))
    assert not s.client.app.state.opa.blobs  # dropped from memory once the worker fetched it
    with s.eng.begin() as c:  # and never stored in the database: task payloads carry a token, not the bytes
        sizes = [len(json.dumps(p)) for (p,) in c.execute(text("select payload from worker_task"))]
    assert max(sizes) < len(data)


@pytest.mark.integration
@pytest.mark.tc("SRCH-PLAN-011")
def test_plan_011_llm_planner_times_out_to_rules(s):
    import time

    s.add(1, caption="something")
    s.index()
    # the worker is registered and online, but nothing serves its queue: the query.plan RPC never returns
    t0 = time.monotonic()  # noqa: TID251 - measures the RPC deadline
    r = s.search(q="那張很有感覺的照片", plan=True, semantic=False)
    took = time.monotonic() - t0  # noqa: TID251
    assert r["planner"]["source"] == "rules"
    assert r["planner"]["confidence"] < 0.6
    assert 1.4 <= took < 3.0  # waited the 1.5 s planner budget, then used the rules
    with s.eng.begin() as c:
        kinds = [k for (k,) in c.execute(text("select kind from worker_task where kind = 'query_plan'"))]
    assert kinds == ["query_plan"]


@pytest.mark.integration
@pytest.mark.tc("SRCH-TOK-009")
def test_srch_tok_009_nul_and_control_characters_do_not_crash(s):
    """回歸：查詢含 NUL 時 PostgreSQL 拒絕寫入 JSON，整個搜尋回 500（MCP-TOOL 產生輸入時發現）。"""
    aid = s.add(1, caption="海邊的貓")
    s.index()
    for q in ("\x00", "海邊\x00的貓", "\x07貓"):
        r = s.client.post("/api/v1/search", json={"q": q, "plan": True})
        assert r.status_code == 200, (q, r.text)
    assert aid in [x["asset_id"] for x in s.search(q="海邊\x00的貓", semantic=False)["items"]]


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-008")
def test_srch_sem_008_weak_vector_hits_are_related_not_results(s, monkeypatch):
    """回歸 (ADR-0027): the vector route took the K nearest photos with no floor, so every query matched the whole
    library: 「一隻熊」 reported 80 of 80 photos. Below the space's floor a photo is only "possibly related"."""
    import opagent_api.search as S

    monkeypatch.setattr(S, "vector_floor", lambda _st, _space: 0.5)
    targets = {s.add(i, groups=["dog", "beach"]) for i in range(3)}
    others = {s.add(i, groups=["city"]) for i in range(3, 10)}
    s.index()
    stop = serve(s.w)
    try:
        res = s.search(q="a dog on the beach", limit=5)
        none = s.search(q="zzz", limit=5)
    finally:
        stop.set()
    assert {x["asset_id"] for x in res["items"]} == targets
    assert res["total"] == 3 and not res["total_capped"]
    assert res["related"] == []  # below the floor is not shown at all (ADR-0027 amended 2026-11-10 by the owner)
    assert not {x["asset_id"] for x in res["items"]} & others
    assert none["total"] == 0 and none["items"] == []
    assert none["related"] == []  # nothing found says so: unrelated photos were not "possibly related"


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-008")
def test_srch_sem_008_a_space_with_no_floor_keeps_every_neighbour(s):
    aid = s.add(1, groups=["city"])
    s.index()
    stop = serve(s.w)
    try:
        res = s.search(q="a dog on the beach", limit=5)
    finally:
        stop.set()
    assert [x["asset_id"] for x in res["items"]] == [aid] and res["related"] == []


@pytest.mark.unit
@pytest.mark.tc("SRCH-SEM-009")
def test_srch_sem_009_the_floor_comes_from_the_catalogue_entry():
    """Query time only: changing it needs no re-embedding (invariant 7)."""
    from types import SimpleNamespace

    from opagent_api.search import vector_floor
    from opagent_registry.loader import load_registry

    st = SimpleNamespace(registry=load_registry)
    assert vector_floor(st, SimpleNamespace(fingerprint="siglip2-so400m-384@onnx-fp16:21086ca63010")) == 0.07
    assert vector_floor(st, SimpleNamespace(fingerprint="fake-embed")) is None


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-011")
def test_srch_sem_011_chinese_reaches_the_image_model_in_english(s, monkeypatch):
    import opagent_api.search as search_mod

    asked = []
    real = search_mod.query_vector

    def spy(st, text_q, space, modality="image"):
        asked.append((modality, text_q))
        return real(st, text_q, space, modality)

    monkeypatch.setattr(search_mod, "query_vector", spy)
    s.add(1, caption="沙灘上的狗", groups=["dog", "beach"])
    s.index()
    s.search(q="海邊")
    assert ("image", "beach") in asked
    asked.clear()
    res = s.search(q="長頸鹿")
    assert [t for m, t in asked if m == "image"] == []
    assert "image_vector" not in res["degraded"]  # skipped on purpose is not a fault


@pytest.mark.integration
@pytest.mark.tc("SRCH-TOK-010")
def test_srch_tok_010_beach_photos_by_any_name(s):
    aid = s.add(1, caption="沙灘上有人放風箏", groups=[])
    s.add(2, caption="教室裡的積木", groups=[])
    s.index()
    assert [x["asset_id"] for x in s.search(q="海邊", semantic=False)["items"]] == [aid]


@pytest.mark.integration
@pytest.mark.tc("SRCH-TOK-012")
def test_srch_tok_012_a_new_tokenizer_reindexes_the_text(s):
    """回歸: TOKENIZER_VERSION said "bump it and reindex", but nothing reindexed, so old photos kept old words."""
    from opagent_api.labels import retokenize
    from opagent_search.tokenize import TOKENIZER_VERSION

    aid = s.add(1, caption="一隻柴犬在草地上奔跑", groups=[])
    s.index()
    s.sql("update search_doc set tsv = ''::tsvector where asset_id = :a", a=aid)  # as an older tokenizer left it
    s.sql("update workspace set settings = jsonb_set(coalesce(settings, '{}'), '{search_tokenizer}', '\"old\"')")
    assert retokenize(s.client.app.state.opa) == 1
    assert [x["asset_id"] for x in s.search(q="草地", semantic=False)["items"]] == [aid]
    assert s.sql("select settings->>'search_tokenizer' from workspace").scalar() == TOKENIZER_VERSION
    assert retokenize(s.client.app.state.opa) == 0  # once


def _kids_library(s):
    a = s.add(1, caption="小朋友在操場上踢足球", tags=("people.child", "activity.soccer"))
    b = s.add(2, caption="小朋友在教室玩積木", tags=("people.child", "object.blocks"))
    c = s.add(3, caption="空無一人的足球場", tags=("activity.soccer",))
    d = s.add(4, caption="小女孩戴著口罩看書", tags=("people.child",))
    s.index()
    return a, b, c, d


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-001")
def test_srch_cnc_001_every_concept_must_match(s):
    """回歸 (audit round 2): 「小孩在玩水」, 「戴口罩的小女孩」, 「小朋友踢足球」 each returned all 558 child photos:
    the tag route let people.child alone qualify a photo (ADR-0032)."""
    a, b, c, d = _kids_library(s)
    res = s.search(q="小朋友踢足球的照片", semantic=False, facets=["category"])
    assert [x["asset_id"] for x in res["items"]] == [a] and res["total"] == 1
    assert sum((res["facets"].get("category") or {}).values()) == res["total"]  # the set is the count (ADR-0005)
    assert [x["asset_id"] for x in s.search(q="戴口罩的小女孩", semantic=False)["items"]] == [d]


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-002")
def test_srch_cnc_002_a_word_nothing_has_is_set_aside_and_said(s):
    a, b, c, d = _kids_library(s)
    res = s.search(q="小朋友 量子", semantic=False)
    assert {x["asset_id"] for x in res["items"]} == {a, b, d}
    assert res["relaxed"] == ["量子"]


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-003")
def test_srch_cnc_003_one_concept_is_as_before(s):
    a, b, c, d = _kids_library(s)
    assert {x["asset_id"] for x in s.search(q="足球", semantic=False)["items"]} == {a, c}
    assert {x["asset_id"] for x in s.search(q="小孩", semantic=False)["items"]} == {a, b, d}  # 小朋友 is 小孩
