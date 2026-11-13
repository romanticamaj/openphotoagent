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


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-004")
def test_srch_cnc_004_caption_vectors_answer_for_a_word_no_caption_uses(s, monkeypatch):
    """回歸 (audit round 3): 「小孩在玩水」 dropped 玩水 (no caption says it) and showed all 561 child photos, while the
    caption vectors, which read the whole sentence, had found the photos of children in a pool."""
    import opagent_api.search as S

    pool = s.add(1, caption="小朋友在泳池戲水", tags=("people.child",))
    room = s.add(2, caption="小朋友在教室", tags=("people.child",))
    s.index()
    sid = s.sql(
        "insert into embedding_space(model_key, fingerprint, dim, modality, status)"
        " values ('fake-cap', 'caption:fake-cap:x', 2, 'caption', 'active') returning id"
    ).scalar()
    for aid, v in ((pool, "[1,0]"), (room, "[0,1]")):
        s.sql(
            "insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, CAST(:v AS vector))",
            a=aid,
            s=sid,
            v=v,
        )
    monkeypatch.setattr(
        S, "query_vector", lambda st, t, space, modality="image": [1.0, 0.0] if modality == "caption" else None
    )
    monkeypatch.setattr(S, "caption_floor", lambda _st, _sp: 0.5)
    res = s.search(q="小孩在玩水")
    assert [x["asset_id"] for x in res["items"]] == [pool] and res["relaxed"] == []


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-005")
def test_srch_cnc_005_a_word_alone_needs_a_closer_meaning(s, monkeypatch):
    """回歸 (audit round 4): 「小孩在玩水」 returned 8 photos, none in water; 「玩沙」 6, none with sand. A single word is a
    vaguer query than a sentence: standing in for a concept it needs 0.56 on the reference library, not the route's
    0.5 (irrelevant photos let in: 1.65% at 0.5, 0.05% at 0.56)."""
    import opagent_api.search as S

    near = s.add(1, caption="小朋友拿著水瓶", tags=("people.child",))
    s.index()
    sid = s.sql(
        "insert into embedding_space(model_key, fingerprint, dim, modality, status)"
        " values ('fake-cap', 'caption:fake-cap:x', 2, 'caption', 'active') returning id"
    ).scalar()
    s.sql(
        "insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, CAST('[0.53,0.848]' AS vector))",
        a=near,
        s=sid,
    )
    monkeypatch.setattr(
        S, "query_vector", lambda st, t, space, modality="image": [1.0, 0.0] if modality == "caption" else None
    )
    monkeypatch.setattr(S, "caption_floor", lambda _st, _sp: 0.5)
    res = s.search(q="小孩在玩水")
    assert res["relaxed"] == ["玩水"]  # 0.53 is not 玩水: said so, not passed off as a match


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-006")
def test_srch_cnc_006_one_unknown_word_is_held_to_the_word_floor(s, monkeypatch):
    """回歸 (audit round 6): 「玩沙」 showed 6 photos of clay and floor play, 「小孩在玩水」 one child with a water bottle.
    A query of one word the vocabulary does not know went to the caption vectors at the route's 0.5; and the word
    floor 0.56 still let 玩水 stand for a water bottle. On the reference library, 17 such words: wrong photos 14 at
    0.5, 1 at 0.56, 0 at 0.58; captions that say the word are found by their text anyway."""
    import opagent_api.search as S

    clay = s.add(1, caption="小女孩專注玩黏土", tags=("people.child",))
    bottle = s.add(2, caption="幾個小朋友拿著水瓶互動", tags=("people.child",))
    s.index()
    sid = s.sql(
        "insert into embedding_space(model_key, fingerprint, dim, modality, status)"
        " values ('fake-cap', 'caption:fake-cap:x', 2, 'caption', 'active') returning id"
    ).scalar()
    for aid, v in ((clay, "[0.545,0.8384]"), (bottle, "[0.57,0.8216]")):
        s.sql(
            "insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, CAST(:v AS vector))",
            a=aid,
            s=sid,
            v=v,
        )
    monkeypatch.setattr(
        S, "query_vector", lambda st, t, space, modality="image": [1.0, 0.0] if modality == "caption" else None
    )
    monkeypatch.setattr(S, "caption_floor", lambda _st, _sp: 0.5)
    assert s.search(q="玩沙")["total"] == 0
    res = s.search(q="小孩在玩水")
    assert res["relaxed"] == ["玩水"]  # said so: the water bottle is shown as a child photo, not as 玩水


@pytest.mark.integration
@pytest.mark.tc("SRCH-R2-008")
def test_srch_r2_008_a_known_word_asks_the_caption_model_in_english_too(s, monkeypatch):
    """回歸 (audit rounds 4-5): 海邊 found 3 photos where beach found 6: the caption model ranks the surfing photos
    lower for the Chinese word. A query whose words are all in the vocabulary also asks with their English names."""
    import opagent_api.search as S

    asked = []
    monkeypatch.setattr(S, "query_vector", lambda st, t, space, modality="image": asked.append((modality, t)))
    import opagent_api.routes_caption as RC

    monkeypatch.setattr(
        RC,
        "caption_space",
        lambda c, status="active": type("Sp", (), {"id": 1, "model_key": "m", "fingerprint": "f"})(),
    )
    s.add(1, caption="x")
    s.index()
    s.search(q="海邊")
    assert ("caption", "beach") in asked
    asked.clear()
    s.search(q="小孩在玩水")  # 玩水 is not in the vocabulary: the words as typed
    assert ("caption", "小孩在玩水") in asked and ("caption", "child") not in asked


@pytest.mark.integration
@pytest.mark.tc("SRCH-R2-009")
def test_srch_r2_009_a_caption_route_at_its_limit_marks_the_total_as_a_floor(s, monkeypatch):
    """回歸 (audit round 20): 「教室」 read 「250 張」 as if exact: the caption-vector route had stopped at its limit, but
    total_capped looked only at the image route."""
    import opagent_api.search as S

    ids = [s.add(i, caption=f"教室 {i}") for i in range(5)]
    s.index()
    sid = s.sql(
        "insert into embedding_space(model_key, fingerprint, dim, modality, status)"
        " values ('fake-cap', 'caption:fake-cap:x', 2, 'caption', 'active') returning id"
    ).scalar()
    for aid in ids:
        s.sql(
            "insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, CAST('[1,0]' AS vector))",
            a=aid,
            s=sid,
        )
    monkeypatch.setattr(S, "PER_ROUTE", 3)
    monkeypatch.setattr(
        S, "query_vector", lambda st, t, space, modality="image": [1.0, 0.0] if modality == "caption" else None
    )
    monkeypatch.setattr(S, "caption_floor", lambda _st, _sp: 0.5)
    res = s.search(q="zzz-no-word-matches")
    assert res["total_capped"] is True


@pytest.mark.integration
@pytest.mark.tc("SRCH-R2-010")
def test_srch_r2_010_a_few_results_are_not_called_the_most_relevant(s, monkeypatch):
    """回歸 (audit round 23): 「小孩踢球很開心」 read 「最相關的 2 張」: a vector route stopped at its limit, then
    requiring every concept left 2. Fewer than the limit is the whole answer, not the top of a longer list."""
    import opagent_api.search as S

    ids = [s.add(i, caption=f"教室 喵喵 {i}" if i < 2 else f"教室 {i}") for i in range(5)]
    s.index()
    sid = s.sql(
        "insert into embedding_space(model_key, fingerprint, dim, modality, status)"
        " values ('fake-cap', 'caption:fake-cap:x', 2, 'caption', 'active') returning id"
    ).scalar()
    for aid in ids:
        s.sql(
            "insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, CAST('[1,0]' AS vector))",
            a=aid,
            s=sid,
        )
    monkeypatch.setattr(S, "PER_ROUTE", 3)
    monkeypatch.setattr(
        S, "query_vector", lambda st, t, space, modality="image": [1.0, 0.0] if modality == "caption" else None
    )
    monkeypatch.setattr(S, "caption_floor", lambda _st, _sp: 0.5)
    monkeypatch.setattr(S, "_caption_meaning", lambda *a: {})  # 喵 by its text only
    res = s.search(q="教室 喵喵")  # the caption route stopped at 3; both words leave 2 (喵喵: not in the vocabulary)
    assert res["total"] == 2 and res["total_capped"] is False


def _soccer(s, smiling=1):
    ids = [
        s.add(
            i,
            caption=f"小朋友在草地上踢足球 {i}",
            tags=("people.child", "activity.soccer", *(("mood.smile",) if i < smiling else ())),
        )
        for i in range(3)
    ]
    s.add(9, caption="小朋友開心地笑", tags=("people.child", "mood.smile"))
    s.index()
    return ids


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-007")
def test_srch_cnc_007_an_expression_ranks_but_does_not_filter(s):
    """回歸 (audit round 23): 「小孩踢球很開心」 found 2 where 「小孩踢球」 found 175: a smile tag on small, moving faces is
    seldom given, and its absence is not a frown (ADR-0032 addendum 2026-11-11)."""
    ids = _soccer(s, smiling=1)
    res = s.search(q="小孩踢球很開心", semantic=False)
    assert res["total"] == 3 and res["items"][0]["asset_id"] == ids[0]  # the smile first
    assert res["soft"] == [{"word": "開心", "key": "mood.smile", "shown": 1}]


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-008")
def test_srch_cnc_008_an_expression_alone_still_filters(s):
    _soccer(s, smiling=1)
    res = s.search(q="開心", semantic=False)
    assert res["total"] == 2 and res.get("soft", []) == []


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-009")
def test_srch_cnc_009_no_photo_shows_the_expression(s):
    _soccer(s, smiling=0)
    res = s.search(q="小孩踢球很開心", semantic=False)
    assert res["total"] == 3 and res["soft"] == [{"word": "開心", "key": "mood.smile", "shown": 0}]


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-011")
def test_srch_cnc_011_the_vectors_are_not_asked_the_expression(s, monkeypatch):
    """回歸 (audit round 24): 「小孩踢球很開心」 put beads and a book among the happy photos: the image and caption models
    were asked 「soccer smile child」, so happy children who were not playing ball came in by their vectors."""
    import opagent_api.search as S

    asked = []
    monkeypatch.setattr(S, "query_vector", lambda st, t, space, modality="image": asked.append((modality, t)))
    import opagent_api.routes_caption as RC

    monkeypatch.setattr(
        RC,
        "caption_space",
        lambda c, status="active": type("Sp", (), {"id": 1, "model_key": "m", "fingerprint": "f"})(),
    )
    _soccer(s, smiling=1)
    s.search(q="小孩踢球很開心")
    texts = [t for m, t in asked if m in ("image", "caption")]
    assert texts and not any("smile" in t or "開心" in t for t in texts), texts


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-012")
def test_srch_cnc_012_only_the_expression_counts_what_the_divider_said(s):
    """回歸 (audit round 24): 「只看開心（23 張）」 gave 19, and most were not playing ball: the button added a tag filter,
    which neither kept the other concepts' set nor counted the captions that say it."""
    ids = _soccer(s, smiling=2)
    res = s.search(q="小孩踢球很開心", semantic=False)
    shown = res["soft"][0]["shown"]
    only = s.search(q="小孩踢球很開心", semantic=False, soft_only=True)
    assert only["total"] == shown == 2 and {x["asset_id"] for x in only["items"]} == set(ids[:2])


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-007")
def test_srch_sem_007_weak_look_alikes_go_last_and_are_counted(s, monkeypatch):
    """回歸 (audit round 26): the last ~35 of 233 for 「kids playing soccer」 were children crawling or running. On the
    reference library, photos found by the image model alone more than 0.06 under the best were 2-30% relevant, those
    within it 75-100% (ADR-0040)."""
    import opagent_api.search as S

    a = s.add(1, caption="甲")
    b = s.add(2, caption="乙")
    c = s.add(3, caption="丙")
    d = s.add(4, caption="zebra on grass")  # found by its words too: never weak
    s.index()
    sid = s.sql(
        "insert into embedding_space(model_key, fingerprint, dim, modality, status)"
        " values ('fake-img', 'fake-img@x', 2, 'image', 'active') returning id"
    ).scalar()
    for aid, v in ((a, "[1,0]"), (b, "[0.97,0.2431]"), (c, "[0.9,0.4359]"), (d, "[0.88,0.475]")):
        s.sql(
            "insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, CAST(:v AS vector))",
            a=aid,
            s=sid,
            v=v,
        )
    monkeypatch.setattr(
        S, "query_vector", lambda st, t, space, modality="image": [1.0, 0.0] if modality == "image" else None
    )
    monkeypatch.setattr(S, "vector_floor", lambda _st, _sp: 0.0)
    res = s.search(q="zebra")
    order = [x["asset_id"] for x in res["items"]]
    assert order[-1] == c and set(order[:3]) == {a, b, d}
    assert res.get("weak") == 1 and res.get("weak_from") == 3 and res["total"] == 4


def _surf(s):
    beach = [s.add(i, caption="在海邊衝浪", tags=("activity.surfing", "scene.beach")) for i in range(2)]
    sea = [s.add(i + 10, caption="在浪上衝浪", tags=("activity.surfing", "scene.sea")) for i in range(2)]
    plain = [s.add(20, caption="一個人衝浪", tags=("activity.surfing",))]
    s.add(30, caption="海邊散步", tags=("scene.beach",))
    s.add(31, caption="車頂上的衝浪板", tags=("object.surfboard",))
    s.index()
    return beach, sea, plain


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-013")
def test_srch_cnc_013_a_scene_the_activity_implies_ranks(s):
    """回歸 (audit rounds 32-35): 「有沒有在海邊衝浪的照片？」 found 2 where 衝浪 found 5: surfing in the waves shows no
    sand. Surfing implies the sea or a beach; asked together, the beach ranks and does not filter."""
    beach, sea, plain = _surf(s)
    res = s.search(q="在海邊衝浪", semantic=False)
    order = [x["asset_id"] for x in res["items"]]
    assert res["total"] == 5 and set(order[:2]) == set(beach)
    assert res["soft"] == [{"word": "海邊", "key": "scene.beach", "shown": 2, "implied_by": "衝浪"}]


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-014")
def test_srch_cnc_014_an_object_or_a_word_outside_the_vocabulary_implies_nothing(s):
    _surf(s)
    res = s.search(q="海邊衝浪板", semantic=False)  # a board is not a wave
    assert res["total"] == 0 or all("scene.beach" in (x["tags"] or []) for x in res["items"])
    assert all(x.get("implied_by") is None for x in res.get("soft") or [])


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-015")
def test_srch_cnc_015_the_vectors_are_not_asked_the_implied_scene(s, monkeypatch):
    """回歸 (audit round 36): 「海邊衝浪」 gave 8, the first two a kite and horses on a beach: the image and caption models
    were asked 「beach surfing」, and their beach photos came in. They are asked the activity only."""
    import opagent_api.search as S

    asked = []
    monkeypatch.setattr(S, "query_vector", lambda st, t, space, modality="image": asked.append((modality, t)))
    import opagent_api.routes_caption as RC

    monkeypatch.setattr(
        RC,
        "caption_space",
        lambda c, status="active": type("Sp", (), {"id": 1, "model_key": "m", "fingerprint": "f"})(),
    )
    _surf(s)
    s.search(q="在海邊衝浪")
    texts = [t for m, t in asked if m in ("image", "caption")]
    assert texts and not any("beach" in t or "海邊" in t or "sea" in t.split() for t in texts), texts


@pytest.mark.unit
@pytest.mark.tc("SRCH-SEM-010")
def test_srch_sem_010_photos_shown_first_are_never_the_weak_ones():
    """回歸 (audit round 39): 「小孩踢球很開心」 folded the 11 happy photos as 「較不相關」. They came in through the
    expression (no route had them, so nothing was recorded as finding them) and an empty set passed 「the image model
    alone」. Weak means found by the image model alone, far under the best, and not among the photos shown first."""
    from opagent_api.search import weak_ids

    cos = {"a": 0.30, "b": 0.29, "happy": 0.10, "far": 0.12}
    matched = {"a": ["image_vector"], "b": ["image_vector", "tag"], "far": ["image_vector"]}
    ranked = ["happy", "a", "b", "far"]
    assert weak_ids(ranked, matched, cos, first={"happy"}) == ["far"]
    assert weak_ids(["a"], {"a": ["image_vector"]}, {"a": 0.3}, first=set()) == []  # the best is not weak


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-016")
def test_srch_cnc_016_when_no_word_is_shown_the_meaning_answers(s, monkeypatch):
    """回歸 (audit round 42): 「一張有很多彩色方塊的圖」 gave 1 unrelated photo: no photo's text said 彩色 or 方塊, so
    every word was set aside (ADR-0032) and nothing was left to match. Then the whole phrase's caption meaning answers,
    and the response says so."""
    import opagent_api.search as S

    near = [s.add(i, caption=f"幾何色塊拼貼 {i}") for i in range(2)]
    s.add(5, caption="小狗在草地")
    s.index()
    sid = s.sql(
        "insert into embedding_space(model_key, fingerprint, dim, modality, status)"
        " values ('fake-cap', 'caption:fake-cap:x', 2, 'caption', 'active') returning id"
    ).scalar()
    for aid, v in ((near[0], "[1,0]"), (near[1], "[0.95,0.3122]")):
        s.sql(
            "insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, CAST(:v AS vector))",
            a=aid,
            s=sid,
            v=v,
        )
    monkeypatch.setattr(
        S, "query_vector", lambda st, t, space, modality="image": [1.0, 0.0] if modality == "caption" else None
    )
    monkeypatch.setattr(S, "caption_floor", lambda _st, _sp: 0.5)
    monkeypatch.setattr(S, "_caption_meaning", lambda *a: {})  # no single word means it on its own
    res = s.search(q="喵喵很多嘟嘟")
    assert {x["asset_id"] for x in res["items"]} == set(near) and res["total"] == 2
    assert res.get("meaning_only") is True


@pytest.mark.integration
@pytest.mark.tc("SRCH-IDX-010")
def test_srch_idx_010_a_photo_dated_by_its_file_is_found_by_date(s):
    """回歸 (audit round 45): 「11月」 found 0 while the timeline showed 80 photos in 2026-11: photos with no capture
    date sit on the timeline by their file time (local_at), but the search index kept taken_at only."""
    a = s.add(1, caption="海邊")
    s.sql("update asset set taken_at = null, date_source = 'mtime', local_at = '2026-11-03 10:00' where id = :a", a=a)
    s.index()
    res = s.search(filters={"taken_from": "2026-11-01", "taken_to": "2026-12-01"}, semantic=False)
    assert [x["asset_id"] for x in res["items"]] == [a]


@pytest.mark.integration
@pytest.mark.tc("SRCH-PLAN-026")
def test_srch_plan_026_a_day_without_a_year_is_found_in_every_year(s):
    """回歸 (audit rounds 46-48): 「11月10日的貓」 found 0: with no year it looked at this year only."""
    a = s.add(1, caption="貓", taken=datetime(2024, 11, 10, 3, tzinfo=UTC))
    b = s.add(2, caption="貓", taken=datetime(2025, 11, 10, 3, tzinfo=UTC))
    s.add(3, caption="貓", taken=datetime(2025, 11, 12, 3, tzinfo=UTC))
    s.index()
    res = s.search(q="11月10日的貓", plan=True, semantic=False)
    assert {x["asset_id"] for x in res["items"]} == {a, b}


def _near(s, cosines, captions=None, tags=None):
    """Photos at these cosines to the query [1, 0] in a fake image space."""
    import math

    ids = [
        s.add(i, caption=(captions or {}).get(i, f"圖{i}"), tags=(tags or {}).get(i, ())) for i in range(len(cosines))
    ]
    s.index()
    sid = s.sql(
        "insert into embedding_space(model_key, fingerprint, dim, modality, status)"
        " values ('fake-img', 'fake-img@x', 2, 'image', 'active') returning id"
    ).scalar()
    for aid, cos in zip(ids, cosines, strict=True):
        s.sql(
            "insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, CAST(:v AS vector))",
            a=aid,
            s=sid,
            v=f"[{cos},{math.sqrt(1 - cos * cos)}]",
        )
    return ids


def _gate(monkeypatch, alone):
    import opagent_api.search as S

    monkeypatch.setattr(
        S, "query_vector", lambda st, t, space, modality="image": [1.0, 0.0] if modality == "image" else None
    )
    monkeypatch.setattr(S, "vector_floor", lambda _st, _sp: 0.07)
    monkeypatch.setattr(S, "vector_alone", lambda _st, _sp: alone)


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-012")
def test_srch_sem_012_a_weak_image_only_answer_is_no_answer(s, monkeypatch):
    """回歸 (audit round 51): 「夜景」 said 「20 張」, all abstract collages at 0.077 just over the 0.07 floor: no word,
    tag or caption meaning matched anything, and the image model's best was weak. Alone, the image route needs its
    best over `min_cosine_alone` (ADR-0042); then the floor judges the rest as before."""
    _near(s, [0.077, 0.077, 0.077, 0.075, 0.075, 0.069])
    _gate(monkeypatch, 0.082)
    res = s.search(q="night", facets=["tags"])
    assert res["total"] == 0 and res["items"] == [] and res.get("image_only_weak") is True
    assert not res.get("facets", {}).get("tags")


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-013")
def test_srch_sem_013_a_strong_best_keeps_the_floor_for_the_rest(s, monkeypatch):
    """ADR-0047 (superseding ADR-0042's 「the floor judges the rest」): with no word hits each image-only photo needs the
    bar, not only the best: 0.087 counts; 0.070, above the floor, is 「可能相關」; 0.065 is below the floor."""
    ids = _near(s, [0.087, 0.070, 0.065])
    _gate(monkeypatch, 0.082)
    res = s.search(q="night")
    assert res["total"] == 1 and {x["asset_id"] for x in res["items"]} == {ids[0]}
    assert [x["asset_id"] for x in res["possible"]] == [ids[1]]
    assert not res.get("image_only_weak")


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-014")
def test_srch_sem_014_no_gate_when_words_match_or_the_catalog_has_none(s, monkeypatch):
    """A word match counts, and its photo is never gated; with fewer than 3 word hits the image-only ones each need the
    bar (ADR-0047: two 0.077 collages are 「可能相關」, not counted, as they inflated 「城市」). A model without the key
    keeps the floor alone."""
    _near(s, [0.077, 0.077, 0.05], captions={2: "city at night"})
    _gate(monkeypatch, 0.082)
    res = s.search(q="night")
    assert res["total"] == 1 and res["possible_total"] == 2
    _gate(monkeypatch, None)
    assert s.search(q="nights")["total"] >= 2


@pytest.mark.unit
@pytest.mark.tc("SRCH-SEM-014")
def test_srch_sem_014_the_bar_is_per_model_in_the_catalog():
    from types import SimpleNamespace

    from opagent_api.search import vector_alone
    from opagent_registry.loader import load_registry

    st = SimpleNamespace(registry=load_registry)
    assert vector_alone(st, SimpleNamespace(fingerprint="siglip2-so400m-384@abc")) == 0.082
    assert vector_alone(st, SimpleNamespace(fingerprint="qwen3-embedding-0.6b@abc")) is None


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-015")
def test_srch_sem_015_a_phrase_no_photo_shows_in_words_needs_a_strong_image_answer(s, monkeypatch):
    """回歸 (audit round 52): 「城市夜景」 and 「city night」 still showed two abstract collages, found by the image model
    alone at 0.077: photos say 城市 and one says 夜景, but none says both, so the whole phrase rests on the image model,
    as one word did in SRCH-SEM-012 (ADR-0042 addendum)."""
    ids = _near(
        s,
        [0.077, 0.077, 0.03, 0.03, 0.02],
        captions={2: "城市街景", 3: "城市的街道", 4: "山上的夜景"},
        tags={2: ("scene.city",), 3: ("scene.city",), 4: ("scene.night",)},  # the tag route finds some: no gate
    )
    _gate(monkeypatch, 0.082)
    for q in ("城市夜景", "city night"):
        res = s.search(q=q)
        assert not {x["asset_id"] for x in res["items"]} & set(ids[:2]), (q, res["items"])


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-017")
def test_srch_cnc_017_cars_buses_trains_and_bicycles_are_all_車子(s):
    """回歸 (audit rounds 57-58): 「車子」 found 2 of about 11 vehicle photos. It includes its kinds; a plane is not
    one, and 「汽車」 is still a car only. The included photos are members and count."""
    s.add(1, caption="小朋友搭公車")
    s.add(2, caption="一家人坐捷運")
    s.add(3, caption="騎腳踏車到公園")
    s.add(4, caption="路邊停著一輛汽車")
    s.add(5, caption="準備搭飛機出國")
    s.index()
    res = s.search(q="車子", semantic=False)
    assert res["total"] == 4, [x["caption"] for x in res["items"]]
    assert "準備搭飛機出國" not in {x["caption"] for x in res["items"]}
    assert s.search(q="汽車", semantic=False)["total"] == 1
    assert s.search(q="公車", semantic=False)["total"] == 1
    assert s.search(q="小朋友 車子", semantic=False)["total"] == 1  # every concept still has to show (ADR-0032)


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-018")
def test_srch_cnc_018_a_tram_and_traffic_are_車子_too(s):
    """回歸 (audit round 59): 「車子」 missed a tram tagged 電車 and two street photos tagged 車輛, the very words for
    them: 車輛 names 車子 itself and 電車 is a kind of 火車."""
    s.add(1, caption="電車行駛在街道上", tags=("電車",))
    s.add(2, caption="高架橋下的道路", tags=("車輛", "道路"))
    s.add(3, caption="城市街景", tags=("車輛",))
    s.add(4, caption="海邊的夕陽")
    s.index()
    assert s.search(q="車子", semantic=False)["total"] == 3
    assert s.search(q="火車", semantic=False)["total"] == 1


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-016")
def test_srch_sem_016_vectors_count_only_when_they_look_like_the_word_hits(s, monkeypatch):
    """ADR-0043b (one deliberation sub-agent; the owner chose 「0 張確定＋可能相關」 2026-11-12): 「溜滑梯」 said
    「250 張以上」 with 14 photos the words found. With 3 or more word hits, a photo found only by vectors counts when
    its image score is at least the median of the word hits'; the others are 「可能相關」: not counted, not in facets
    or select-all, listed apart."""
    cos = [0.12, 0.13, 0.14, 0.15, 0.16, 0.145, 0.13]
    ids = _near(s, cos, captions={i: "踢足球" for i in range(5)}, tags={i: ("activity.soccer",) for i in range(5)})
    _gate(monkeypatch, 0.082)
    res = s.search(q="足球", facets=["tags"])
    assert res["total"] == 6 and ids[5] in {x["asset_id"] for x in res["items"]}
    assert [x["asset_id"] for x in res.get("possible", [])] == [ids[6]] and res.get("possible_total") == 1
    assert ids[6] not in {x["asset_id"] for x in res["items"]}


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-017")
def test_srch_sem_017_with_few_word_hits_each_vector_photo_needs_the_bar(s, monkeypatch):
    """Fewer than 3 word hits: every photo found by the image alone needs min_cosine_alone (0.082), not only the best;
    the rest above the floor are 「可能相關」. Nothing above the bar at all stays 「直接說沒有」 (ADR-0042)."""
    ids = _near(s, [0.09, 0.085, 0.075])
    _gate(monkeypatch, 0.082)
    res = s.search(q="night")
    assert res["total"] == 2 and {x["asset_id"] for x in res["items"]} == set(ids[:2])
    assert [x["asset_id"] for x in res.get("possible", [])] == [ids[2]]


@pytest.mark.unit
@pytest.mark.tc("SRCH-SEM-018")
def test_srch_sem_018_a_known_words_caption_meaning_keeps_its_floor():
    """回歸 (ADR-0047 measured on the reference library): with the stricter caption bar, 「海邊」 fell from 6 photos,
    all right, to 2: four surfers on the sea were found by the caption meaning alone. The bar plus MEANING_MARGIN was
    measured for words the vocabulary does not know; a known word's caption meaning keeps the route's floor."""
    from opagent_api.search import caption_alone_bar, vector_members

    assert caption_alone_bar(0.5, known=True) is None  # a known word: the route's own floor
    assert abs(caption_alone_bar(0.5, known=False) - 0.58) < 1e-9  # the measured margin, for unknown words
    counted, possible = vector_members(
        ["w1", "w2", "c1", "c2"], {"w1", "w2"}, set(), {}, {"c1": 0.53, "c2": 0.51}, 0.082, caption_alone_bar(0.5, True)
    )
    assert counted == ["c1", "c2"] and possible == []


@pytest.mark.unit
@pytest.mark.tc("SRCH-SEM-019")
def test_srch_sem_019_no_more_than_when_the_long_tail_is_possible():
    """回歸 (ADR-0047): 「溜滑梯」 counted 14 but still said 「14 張以上」: the vector routes filling their limit made it
    capped, though everything past the 14 is in 「可能相關」. Only the counted set can make it 「以上」."""
    from opagent_api.search import still_capped

    assert still_capped(capped=True, word_hits=14, possible=236) is False
    assert still_capped(capped=True, word_hits=250, possible=10) is True  # the words themselves filled the limit
    assert still_capped(capped=True, word_hits=3, possible=0) is True


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-019")
def test_srch_cnc_019_english_words_for_a_concept_find_its_photos(s):
    """回歸 (audit round 68): 「slide」 said 「200 張以上」, every one found by the image model alone, while 「溜滑梯」
    found its 14: the vocabulary knew 溜滑梯 and 盪鞦韆 but not their English. Typed in English, a concept's word finds
    the photos its Chinese word finds."""
    s.add(1, caption="小朋友在溜滑梯", tags=("溜滑梯",))
    s.add(2, caption="盪鞦韆", tags=("盪鞦韆",))
    s.add(3, caption="海邊的夕陽")
    s.index()
    for q in ("slide", "swing", "slides"):
        assert s.search(q=q, semantic=False)["total"] == 2, q


@pytest.mark.integration
@pytest.mark.tc("SRCH-SUG-001")
def test_srch_sug_001_nothing_found_suggests_words_that_find_photos(s):
    """回歸 (audit round 69): 19 queries ended on a bare 「找不到符合的照片」; 「爬山」 found nothing while 「山」 had photos.
    With nothing found, the answer names the vocabulary's words inside the query that do find photos, with how many."""
    s.add(1, caption="山上的風景", tags=("山",))
    s.add(2, caption="海邊的夕陽")
    s.index()
    res = s.search(q="爬山", semantic=False)
    assert res["total"] == 0
    assert res["suggest"] == [{"q": "山", "n": 1}]
    assert s.search(q="夕陽", semantic=False)["suggest"] == []  # something found: nothing to suggest
    assert s.search(q="長頸鹿", semantic=False)["suggest"] == []


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-020")
def test_srch_cnc_020_eating_a_food_is_that_food(s):
    """回歸 (audit round 71): 「吃蛋糕」 found 10 photos and 「蛋糕」 1: 吃 stood as a concept of its own beside the food,
    and its caption meaning let in toy-play scenes. A food implies eating: beside one, 吃 ranks and never adds photos."""
    s.add(1, caption="桌上的生日蛋糕", tags=("蛋糕",))
    s.add(2, caption="小朋友在吃午餐", tags=("用餐",))
    s.add(3, caption="海邊的夕陽")
    s.index()
    assert s.search(q="蛋糕", semantic=False)["total"] == 1
    assert s.search(q="吃蛋糕", semantic=False)["total"] == 1
    assert s.search(q="吃冰淇淋", semantic=False)["total"] == 0


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-021")
def test_srch_cnc_021_eating_an_unknown_food_finds_that_food(s):
    """回歸 (audit round 72): 「吃香蕉」 said 「沒有照片拍到「香蕉」」 while 「香蕉」 alone found 3."""
    s.add(1, caption="一根香蕉放在桌上")
    s.add(2, caption="小朋友在吃午餐", tags=("用餐",))
    s.index()
    assert s.search(q="香蕉", semantic=False)["total"] == 1
    res = s.search(q="吃香蕉", semantic=False)
    assert res["total"] == 1 and not res["relaxed"]


@pytest.mark.integration
@pytest.mark.tc("SRCH-CNC-022")
def test_srch_cnc_022_a_verb_before_a_thing_finds_that_thing(s):
    """回歸 (audit round 73): 「去爬山」 said 「沒有照片拍到「去」」 and 「符合其他條件的有 0 張」."""
    s.add(1, caption="週末郊山健行", tags=("爬山",))
    s.add(2, caption="海邊的夕陽")
    s.index()
    for q in ("爬山", "去爬山"):
        res = s.search(q=q, semantic=False)
        assert res["total"] == 1 and not res["relaxed"], q


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-020")
def test_srch_sem_020_a_described_photo_the_words_and_meaning_reject_is_only_possible(s, monkeypatch):
    """ADR-0047 addendum 3 (one deliberation sub-agent; hand labels, audit round 75): with no word hits to anchor it, a
    photo found by the image alone counted at 0.082, and on a library the VLM had described that was wrong every time
    (trampoline 0/25, 博物館 0/4, 婚禮 0/1). A photo with a caption vector was read by the words and the caption meaning,
    and both said no: the image alone does not count it, it is 「可能相關」. A photo without one (fast mode) still
    counts by the image alone."""
    import opagent_api.search as S

    ids = _near(s, [0.09, 0.09])
    cid = s.sql(
        "insert into embedding_space(model_key, fingerprint, dim, modality, status)"
        " values ('fake-cap', 'caption:fake-cap:x', 2, 'caption', 'active') returning id"
    ).scalar()
    s.sql(  # described, and far from the query's meaning
        "insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, CAST('[0,1]' AS vector))", a=ids[0], s=cid
    )
    monkeypatch.setattr(S, "query_vector", lambda st, t, space, modality="image": [1.0, 0.0])
    monkeypatch.setattr(S, "vector_floor", lambda _st, _sp: 0.07)
    monkeypatch.setattr(S, "vector_alone", lambda _st, _sp: 0.082)
    monkeypatch.setattr(S, "caption_floor", lambda _st, _sp: 0.5)
    res = s.search(q="trampoline")
    counted = {x["asset_id"] for x in res["items"]}
    assert counted == {ids[1]}, res["items"]
    assert [x["asset_id"] for x in res.get("possible", [])] == [ids[0]]


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-022")
def test_srch_sem_022_a_pair_fallback_hit_does_not_make_an_anchor(s, monkeypatch):
    """回歸 (audit round 77 → 78): 「海邊」 fell from 6 to 4. A character-pair hit (「沙灘排球」 on a sports field) was
    its third word hit, which made an anchor of image scores and sent its surfing photos, found by the caption meaning,
    to 「可能相關」. Pairs are the weakest words: only word and tag hits sample what the query looks like."""
    import opagent_api.search as S

    ids = _near(
        s,
        [0.12, 0.12, 0.12, 0.05],
        captions={0: "海邊玩水", 1: "海邊散步", 2: "沙灘排球", 3: "一名男子在浪上"},
        tags={0: ("海邊",), 1: ("海邊",)},
    )
    cid = s.sql(
        "insert into embedding_space(model_key, fingerprint, dim, modality, status)"
        " values ('fake-cap', 'caption:fake-cap:x', 2, 'caption', 'active') returning id"
    ).scalar()
    for i, aid in enumerate(ids):
        v = "[1,0]" if i == 3 else "[0,1]"
        s.sql(
            "insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, CAST(:v AS vector))",
            a=aid,
            s=cid,
            v=v,
        )
    monkeypatch.setattr(S, "query_vector", lambda st, t, space, modality="image": [1.0, 0.0])
    monkeypatch.setattr(S, "vector_floor", lambda _st, _sp: 0.07)
    monkeypatch.setattr(S, "vector_alone", lambda _st, _sp: 0.082)
    monkeypatch.setattr(S, "caption_floor", lambda _st, _sp: 0.5)
    res = s.search(q="海邊")
    assert ids[3] in {x["asset_id"] for x in res["items"]}, res.get("possible")


@pytest.mark.integration
@pytest.mark.tc("SRCH-SEM-023")
def test_srch_sem_023_a_described_photo_needs_the_upper_quartile_of_the_word_hits(s, monkeypatch):
    """ADR-0047 addendum 5 (hand labels, audit round 75; audit round 78): with an anchor, a photo the VLM described and
    the words and caption meaning both passed over counted at the word hits' median, and the median let in courts
    (籃球 17, 公園 44). On the labels the upper quartile kept 書 3/0 and 花 3/0 where the median kept 7/3 and 4/3. A
    described photo needs the upper quartile; one without a caption vector keeps the median (SRCH-SEM-016)."""
    import opagent_api.search as S

    cos = [0.12, 0.13, 0.14, 0.15, 0.16, 0.145, 0.145]
    ids = _near(s, cos, captions={i: "踢足球" for i in range(5)}, tags={i: ("activity.soccer",) for i in range(5)})
    cid = s.sql(
        "insert into embedding_space(model_key, fingerprint, dim, modality, status)"
        " values ('fake-cap', 'caption:fake-cap:x', 2, 'caption', 'active') returning id"
    ).scalar()
    s.sql(
        "insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, CAST('[0,1]' AS vector))", a=ids[6], s=cid
    )
    monkeypatch.setattr(S, "query_vector", lambda st, t, space, modality="image": [1.0, 0.0])
    monkeypatch.setattr(S, "vector_floor", lambda _st, _sp: 0.07)
    monkeypatch.setattr(S, "vector_alone", lambda _st, _sp: 0.082)
    monkeypatch.setattr(S, "caption_floor", lambda _st, _sp: 0.5)
    res = s.search(q="足球")
    counted = {x["asset_id"] for x in res["items"]}
    assert ids[5] in counted  # not described: the median (0.14)
    assert ids[6] not in counted and ids[6] in {x["asset_id"] for x in res.get("possible", [])}  # described: 0.15


@pytest.mark.integration
@pytest.mark.tc("SRCH-FAC-006")
def test_srch_fac_006_a_filtered_tag_is_named_too(s):
    """回歸 (audit round 81): a memory theme's 「看全部」 showed the chip 「activity.crafts」: the names came only for the
    facets' tags. A tag the search is filtered by is named as well."""
    s.add(1, caption="剪紙", tags=("手工",))
    s.index()
    res = s.search(q=None, filters={"tags_all": ["activity.crafts"]}, semantic=False, facets=[])
    assert res["total"] == 1
    assert res["tag_names"].get("activity.crafts") == "手工"
