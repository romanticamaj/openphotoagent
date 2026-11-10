"""R2 caption text vectors (05 §2; 09b §7.5 SRCH-R2-001..003; ADR-0014): captions are embedded by the worker's
multilingual text embedder; search adds a ``caption_vector`` route (RRF weight 0.8); a new model is indexed into a
new space while search keeps using the old one, and the switch is atomic."""

import threading

import httpx
import pytest
from opagent_inference.fake import FakeBackend
from opagent_search.fusion import BASE_WEIGHTS
from opagent_testing.mcp_harness import M
from opagent_worker.agent import WorkerAgent

QWEN, E5 = "qwen3-embedding-0.6b", "multilingual-e5-small"


class FP(FakeBackend):
    def __init__(self, fp):
        super().__init__(vectors="semantic")
        self._fp = fp

    def describe(self):
        d = super().describe()
        return d.__class__(**{**d.__dict__, "model_fingerprint": self._fp})


@pytest.fixture
def env(embedded_pg, tmp_path, api_server):
    m = M(embedded_pg.dsn, tmp_path)
    m.beach = [m.add(f"t/cat{i}.jpg", caption=f"a cat on the beach {i}") for i in range(3)]
    m.city = [m.add(f"t/dog{i}.jpg", caption=f"a dog in the city {i}") for i in range(2)]
    m.index()
    with api_server(m.client.app) as srv:
        http = httpx.Client(base_url=srv.url, timeout=30, headers=m.ui)
        w = WorkerAgent(http, roots=[], home=tmp_path / "w", worker_id="w1")
        w.text_embedders = {QWEN: FP("qwen-fp"), E5: FP("e5-fp")}
        w.register()
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                if not w.run_once(wait_s=0.1):
                    stop.wait(0.05)

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        m.http = http
        yield m
        stop.set()
        t.join(5)
    m.eng.dispose()


def wait_index(m, pred, n=200):
    import time

    for _ in range(n):
        s = m.http.get("/api/v1/search/caption-index").json()
        if pred(s):
            return s
        time.sleep(0.05)  # noqa: TID251 - a real worker thread
    raise AssertionError(s)


@pytest.mark.integration
@pytest.mark.tc("SRCH-R2-001", "SRCH-R2-002")
def test_r2_001_002_caption_vectors_cross_lingual(env):
    m = env
    assert BASE_WEIGHTS["caption_vector"] == 0.8
    r = m.http.post("/api/v1/search/caption-index", json={"model_key": QWEN})
    assert r.status_code == 202, r.text
    assert r.json()["operation_id"]
    s = wait_index(m, lambda s: (s.get("active") or {}).get("model_key") == QWEN)
    assert s["active"]["count"] == 5  # the first index becomes active by itself
    res = m.http.post("/api/v1/search", json={"q": "海邊的貓", "limit": 10, "semantic": True}).json()
    top = [x["asset_id"] for x in res["items"][:3]]
    assert set(top) == set(m.beach)  # no Chinese words in any caption: only R2 finds them
    assert all("caption_vector" in x["matched_by"] for x in res["items"][:3])


@pytest.mark.integration
@pytest.mark.tc("SRCH-R2-003")
def test_r2_003_blue_green_reindex(env):
    m = env
    m.http.post("/api/v1/search/caption-index", json={"model_key": QWEN})
    old = wait_index(m, lambda s: (s.get("active") or {}).get("model_key") == QWEN)["active"]
    r = m.http.post("/api/v1/search/caption-index", json={"model_key": E5})
    assert r.status_code == 202
    early = m.http.post("/api/v1/search/caption-index/switch")
    s = m.http.get("/api/v1/search/caption-index").json()
    if not s["building"]["ready"]:
        assert early.status_code == 409 and early.json()["code"] == "REINDEX_NOT_READY"
    s = wait_index(m, lambda s: (s.get("building") or {}).get("ready"))
    assert s["active"]["id"] == old["id"]  # still answering from the old space
    assert s["building"]["done"] == s["building"]["total"] == 5 and s["building"]["model_key"] == E5
    hits = m.http.post("/api/v1/search", json={"q": "海邊的貓", "limit": 5}).json()["items"]
    assert {x["asset_id"] for x in hits[:3]} == set(m.beach)
    r = m.http.post("/api/v1/search/caption-index/switch")
    assert r.status_code == 200 and r.json()["active"]["model_key"] == E5
    s = m.http.get("/api/v1/search/caption-index").json()
    assert s["active"]["model_key"] == E5 and s["building"] is None
    assert m.sql("select count(*) from asset_embedding where space_id = :s", s=old["id"]).scalar() == 0
    assert m.sql("select status from embedding_space where id = :s", s=old["id"]).scalar() == "retired"
    op = m.sql("select type, status from operation where type = 'search.reindex' order by created_at desc").first()
    assert op.type == "search.reindex" and op.status == "succeeded"


@pytest.mark.integration
@pytest.mark.tc("SRCH-R2-005")
def test_r2_005_caption_vectors_have_a_floor_too(env, monkeypatch):
    """The caption route added its 200 nearest photos to every result with no floor (flagged in ADR-0027, ADR-0031):
    turning the index on would have brought back "every query matches the library". It reads its model's floor."""
    import opagent_api.search as S

    m = env
    m.http.post("/api/v1/search/caption-index", json={"model_key": QWEN})
    wait_index(m, lambda s: (s.get("active") or {}).get("model_key") == QWEN)
    monkeypatch.setattr(S, "caption_floor", lambda _st, _space: 1.01)  # nothing is that close
    res = m.http.post("/api/v1/search", json={"q": "海邊的貓", "limit": 10, "semantic": True}).json()
    assert not any("caption_vector" in x["matched_by"] for x in res["items"])
