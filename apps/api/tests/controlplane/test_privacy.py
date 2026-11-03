"""The four egress levels, enforced by the remote worker before anything leaves it (02 §1.4; 09b §8.3)."""

import io
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_inference.fake import FakeBackend
from opagent_testing.clock import FakeClock
from opagent_worker.agent import WorkerAgent
from opagent_worker.remote import EgressFilter, RemoteClient, enroll
from PIL import Image
from sqlalchemy import text

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"
PW = "correct horse battery"
GPS = {1: "N", 2: (25.0, 2.0, 0.0), 3: "E", 4: (121.0, 33.0, 0.0)}


def gps_jpeg(path):
    im = Image.new("RGB", (1600, 1200), (180, 120, 60))
    ex = Image.Exif()
    ex[0x0110] = "TestCam"
    ex.get_ifd(0x8825).update(GPS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=ex.tobytes())
    path.write_bytes(buf.getvalue())


class Recorder:
    """Wraps the worker's HTTP client: every request that actually left the worker."""

    def __init__(self, http):
        self.http, self.sent = http, []
        self.base_url = http.base_url

    def request(self, method, path, **kw):
        self.sent.append((method, path, kw.get("json"), kw.get("content")))
        return self.http.request(method, path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)


class Env:
    def __init__(self, embedded_pg, sandbox_tree, tmp_path, mode, floor=None, **policy):
        self.clock = FakeClock(datetime(2026, 11, 2, 12, 0, tzinfo=UTC))
        self.cp_thumbs = tmp_path / "cp-thumbs"
        self.app = create_app(
            dsn=embedded_pg.dsn,
            clock=self.clock,
            multi_user=True,
            jwt_secret="s" * 40,
            owner=("admin@example.invalid", PW),
            thumbs_dir=self.cp_thumbs,
        )
        ui = TestClient(self.app, base_url="https://testserver")
        r = ui.post("/api/v1/auth/login", json={"email": "admin@example.invalid", "password": PW})
        ui.headers.update({"X-CSRF-Token": r.json()["csrf"], "X-OPA-UI": "1"})
        r = ui.put("/api/v1/workspace/egress-policy", json={"mode": mode, **policy})
        assert r.status_code == 200, r.text
        code = ui.post("/api/v1/workers/enrollment-codes", json={"name": "nas-gpu"}).json()["code"]
        home = tmp_path / "w"
        enroll("https://testserver", code, home, "nas-gpu", http=TestClient(self.app, base_url="https://testserver"))
        self.rec = Recorder(TestClient(self.app, base_url="https://testserver"))
        egress = EgressFilter(floor=floor, log_path=home / "egress-denied.log")
        self.client = RemoteClient(home, http=self.rec, egress=egress, clock=self.clock)
        (sandbox_tree.photos / "p").mkdir()
        for i in range(3):
            shutil.copyfile(FX / f"IMG_{i + 1:04d}.jpg", sandbox_tree.photos / "p" / f"beach-{i}.jpg")
        gps_jpeg(sandbox_tree.photos / "p" / "home-address.jpg")
        self.w = WorkerAgent(
            self.client,
            roots=sandbox_tree.roots,
            home=home,
            backend=FakeBackend(vlm_p=0.9),
            embedder=FakeBackend(scores="high"),
            worker_id=self.client.worker_id,
        )
        self.w.register()
        self.ui = ui
        self.home = home

    def organize(self):
        src = self.ui.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
        r = self.ui.post(
            "/api/v1/jobs",
            json={"kind": "organize", "preset": "by-content-12", "source_id": src, "target": {"root_alias": "sorted"}},
        )
        assert r.status_code in (200, 201, 202), r.text
        for _ in range(200):
            if not (self.w.run_once() | bool(self.w.process_items_once())):
                break

    def sql(self, q):
        with self.app.state.opa.engine.begin() as c:
            return c.execute(text(q)).all()

    def cp_thumb_sizes(self):
        return sorted(max(Image.open(p).size) for p in self.cp_thumbs.rglob("*.webp"))

    def sent_bytes(self):
        return [(p, c) for _, p, _, c in self.rec.sent if c]

    def sent_json(self):
        return json.dumps([b for _, _, b, _ in self.rec.sent if b is not None], default=str)


@pytest.fixture
def env(embedded_pg, sandbox_tree, tmp_path):
    made = []

    def make(mode, **kw):
        e = Env(embedded_pg, sandbox_tree, tmp_path, mode, **kw)
        made.append(e)
        return e

    yield make
    for e in made:
        e.app.state.opa.engine.dispose()


@pytest.mark.integration
@pytest.mark.tc("MU-PRIV-001")
def test_priv_001_full(env):
    e = env("full", strip_gps=False)
    e.organize()
    assert len(e.sql("select id from asset")) == 4
    assert len(e.sql("select asset_id from asset_embedding")) == 4
    assert len(e.sql("select id from result where category is not null")) == 4
    gps = e.sql("select exif from asset where exif ? 'gps'")
    assert len(gps) == 1  # EXIF, GPS included, reaches the CP
    assert 1024 in e.cp_thumb_sizes() or max(e.cp_thumb_sizes()) > 512


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-PRIV-002")
def test_priv_002_thumbs(env):
    e = env("thumbs", strip_gps=True)
    e.organize()
    sizes = e.cp_thumb_sizes()
    assert sizes and max(sizes) <= 512
    assert all(not p.endswith("/p1024") for p, _ in e.sent_bytes())  # never even sent
    assert e.sql("select exif from asset where exif ? 'gps'") == []
    assert "121.0" not in e.sent_json() and '"gps"' not in e.sent_json()
    assert len(e.sql("select asset_id from asset_embedding")) == 4


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-PRIV-003")
def test_priv_003_edge_thumbs(env):
    e = env("edge-thumbs")
    e.organize()
    assert e.sent_bytes() == [] and e.cp_thumb_sizes() == []  # no thumbnail bytes on the CP
    assert "thumbhash" not in e.sent_json()
    assert len(e.sql("select asset_id from asset_embedding")) == 4
    aid = e.sql("select id from asset order by id limit 1")[0][0]
    import threading

    stop = threading.Event()

    def serve():
        while not stop.is_set():
            if not e.w.run_once():
                stop.wait(0.02)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        r = e.ui.get(f"/api/v1/assets/{aid}/media", params={"variant": "t256"})
    finally:
        stop.set()
        t.join(5)
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert max(Image.open(io.BytesIO(r.content)).size) <= 256
    assert e.cp_thumb_sizes() == []  # relayed, not stored
    assert "no-store" in r.headers.get("cache-control", "")


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-PRIV-004")
def test_priv_004_results_only(env):
    e = env("results-only", hash_paths=True)
    e.organize()
    assert e.sent_bytes() == [] and e.cp_thumb_sizes() == []
    assert e.sql("select asset_id from asset_embedding") == []
    rels = [r[0] for r in e.sql("select rel_path from asset_location")]
    assert len(rels) == 4 and all(r.startswith("~h/") for r in rels)
    blob = e.sent_json()
    for leak in ("beach-", "home-address", "121.0", '"gps"', "thumbhash"):
        assert leak not in blob, leak
    assert len(e.sql("select id from result where category is not null")) == 4  # results still arrive
    res = e.ui.post("/api/v1/search", json={"q": "", "limit": 10}).json()
    assert res["total"] == 4 and res.get("facets") is not None  # search degrades to results + facets
    sem = e.ui.post("/api/v1/search", json={"q": "beach", "semantic": True, "limit": 10}).json()
    assert all(not any("vector" in m for m in x["matched_by"]) for x in sem["items"])  # nothing to match against


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-PRIV-005")
def test_priv_005_worker_enforces_policy(env):
    e = env("results-only", hash_paths=True, floor="results-only")
    e.organize()
    # a tampered CP loosens the policy and asks for what results-only forbids
    e.w.on_welcome({"status": "ok", "egress_policy": {"mode": "full", "strip_gps": False}})
    assert e.client.egress.mode == "results-only"  # the local floor wins
    h = e.sql("select content_hash from asset limit 1")[0][0]
    with e.app.state.opa.engine.begin() as c:
        for i, (kind, payload) in enumerate(
            [("thumb_fetch", {"content_hash": h, "variant": "t512"}), ("embed_assets", {}), ("embed_image", {})]
        ):
            c.execute(
                text(
                    "insert into worker_task(id, kind, payload, status) values (:i, :k, CAST(:p AS jsonb), 'pending')"
                ),
                {"i": f"evil-{i}", "k": kind, "p": json.dumps(payload)},
            )
    before = len(e.sent_bytes())
    while e.w.run_once():
        pass
    rows = dict(e.sql("select id, result->>'error' from worker_task where id like 'evil-%'"))
    assert rows == {
        "evil-0": "EGRESS_POLICY_DENIED",
        "evil-1": "EGRESS_POLICY_DENIED",
        "evil-2": "EGRESS_POLICY_DENIED",
    }
    assert len(e.sent_bytes()) == before and e.cp_thumb_sizes() == []
    log = [json.loads(x) for x in (e.home / "egress-denied.log").read_text().splitlines()]
    assert len(log) >= 3 and all(x["mode"] == "results-only" for x in log)
    # even a direct upload attempt through the client is stopped on the worker
    from opagent_worker.remote import EgressDenied

    with pytest.raises(EgressDenied):
        e.client.put(f"/worker-api/v1/thumbs/{h}/t256", content=b"x")
