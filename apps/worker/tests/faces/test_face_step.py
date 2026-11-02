"""The Worker's face step (07 §2.2, §6.3): off by default, one decode shared with the VLM, query embeddings
returned without landing anywhere, purge that removes face thumbnails and destroys the workspace key."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from opagent_inference.fake import FakeBackend
from opagent_inference.fake_faces import FakeFaceBackend
from opagent_worker.agent import WorkerAgent
from sqlalchemy import text

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"
UI = {"X-OPA-UI": "1"}


class W:
    def __init__(self, dsn, tree, tmp):
        self.client = TestClient(create_app(dsn=dsn), base_url="http://127.0.0.1:8080")
        self.eng = engine_for(dsn)
        self.fb = FakeFaceBackend()
        for i, name in enumerate(["IMG_p1.jpg", "IMG_p1_p2.jpg", "IMG_p2.jpg"]):
            shutil.copy(FX / f"IMG_000{i + 1}.jpg", Path(tree.roots[0]["path"]) / name)
        self.w = WorkerAgent(self.client, roots=tree.roots, home=tmp / "w", backend=FakeBackend(),
                             worker_id="w1", face_backend=self.fb)
        self.w.register()
        self.src = self.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
        self.client.post(f"/api/v1/sources/{self.src}/scan")
        while self.w.run_once():
            pass

    def sql(self, q, **p):
        with self.eng.begin() as c:
            return c.execute(text(q), p)

    def label_all(self):
        from opagent_api.organize import start_label_job

        ids = [r[0] for r in self.sql("select id from asset order by id")]
        start_label_job(self.client.app.state.opa, ids, only_unlabeled=False, priority="normal", origin={})
        while self.w.process_items_once():
            pass


@pytest.fixture
def w(embedded_pg, sandbox_tree, tmp_path):
    x = W(embedded_pg.dsn, sandbox_tree, tmp_path)
    yield x
    x.eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("FACE-GATE-001")
def test_gate_001_no_face_model_calls_when_off(w):
    w.label_all()
    assert w.fb.calls == 0 and w.sql("select count(*) from face").scalar() == 0
    assert w.sql("select count(*) from result").scalar() == 3  # the labelling itself ran


@pytest.mark.integration
@pytest.mark.tc("FACE-DET-004", "FACE-GATE-002")
def test_det_004_one_decode_for_vlm_and_faces(w, monkeypatch):
    import opagent_worker.imaging as imaging

    real, opened = imaging.open_image, []
    monkeypatch.setattr(imaging, "open_image", lambda p: (opened.append(p), real(p))[1])
    w.client.post("/api/v1/faces/enable", json={"consent": True, "consent_text_version": "v1"}, headers=UI)
    w.label_all()
    assert len(opened) == 3 and w.fb.calls == 3
    assert w.sql("select count(*) from face").scalar() == 4  # p1, p1+p2, p2


@pytest.mark.integration
@pytest.mark.tc("FACE-FIND-002")
def test_query_embedding_task_returns_vectors_only_in_the_answer(w):
    w.client.post("/api/v1/faces/enable", json={"consent": True, "consent_text_version": "v1"}, headers=UI)
    st = w.client.app.state.opa
    st.blobs["tok"] = (FX / "IMG_0001.jpg").read_bytes()
    out = w.w._do_face_embed_query({"blob": "tok", "name": "IMG_p3.jpg"}, "t1")
    assert len(out["faces"]) == 1 and len(out["faces"][0]["vec"]) == 512
    assert not list((w.w.home).rglob("*query*"))


@pytest.mark.security
@pytest.mark.tc("FACE-PRG-002")
def test_prg_002_purge_destroys_the_key_and_sealed_backups_stay_closed(w, monkeypatch):
    import keyring
    from keyring.backend import KeyringBackend
    from opagent_worker import facevault

    class Mem(KeyringBackend):
        priority = 1
        store: dict = {}

        def set_password(self, s, u, p):
            self.store[(s, u)] = p

        def get_password(self, s, u):
            return self.store.get((s, u))

        def delete_password(self, s, u):
            self.store.pop((s, u), None)

    prev = keyring.get_keyring()
    keyring.set_keyring(Mem())
    try:
        sealed = facevault.seal(b"bio dump", "default")
        assert facevault.unseal(sealed, "default") == b"bio dump"
        (w.w.home / "faces").mkdir(parents=True)
        (w.w.home / "faces" / "f1.webp").write_bytes(b"thumb")
        rep = w.w._do_face_purge({"workspace_id": "default"}, "t2")
        assert rep["key_destroyed"] is True and rep["thumbnails"] == 1 and "vec" not in str(rep)
        assert keyring.get_password(facevault.SERVICE, facevault.key_name("default")) is None
        assert not (w.w.home / "faces").exists()
        with pytest.raises(facevault.KeyMissing):
            facevault.unseal(sealed, "default")
        facevault.new_key("default")  # a fresh key never opens an old file
        with pytest.raises(facevault.SealBroken):
            facevault.unseal(sealed, "default")
    finally:
        keyring.set_keyring(prev)


@pytest.mark.integration
@pytest.mark.tc("FACE-PRG-003")
def test_prg_003_offline_worker_purges_first_when_back(w):
    w.client.post("/api/v1/faces/enable", json={"consent": True, "consent_text_version": "v1"}, headers=UI)
    w.client.post(f"/api/v1/sources/{w.src}/scan")  # ordinary work queued before the purge
    w.client.post("/api/v1/faces/purge", json={"confirm": "Default"}, headers=UI)
    assert w.client.get("/api/v1/faces/settings").json()["worker_purge_pending"] is True
    first = w.client.post("/worker-api/v1/lease", json={"worker_id": "w1", "wait_s": 0}).json()
    assert first["kind"] == "face_purge"
    w.client.post(f"/worker-api/v1/tasks/{first['id']}/complete", json={"ok": True, "result": {}})
    assert w.client.get("/api/v1/faces/settings").json()["worker_purge_pending"] is False
