"""Deleting all face data is verifiable (09b §9.2 SEC-FACE-001; 07 §6.3, §10; 11 #82)."""

import subprocess
from datetime import UTC, datetime

import pytest
from opagent_api.faces import bio_session
from opagent_db.lifecycle import Backups
from opagent_testing.clock import FakeClock
from opagent_testing.pg import find_pg_bin
from opagent_worker.agent import WorkerAgent
from sqlalchemy import text

from .test_faces_api import Faces, _library


class MemKeyring:
    def __init__(self):
        from keyring.backend import KeyringBackend

        class K(KeyringBackend):
            priority = 1
            store: dict = {}

            def set_password(self, s, u, p):
                self.store[(s, u)] = p

            def get_password(self, s, u):
                return self.store.get((s, u))

            def delete_password(self, s, u):
                self.store.pop((s, u), None)

        self.backend = K()


@pytest.fixture
def f(embedded_pg, tmp_path):
    x = Faces(embedded_pg.dsn, tmp_path, clock=FakeClock(datetime(2026, 11, 3, tzinfo=UTC)))
    x.dsn = embedded_pg.dsn
    yield x
    x.eng.dispose()


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("SEC-FACE-001")
def test_sec_face_001_purge_is_verifiable(f, tmp_path):
    import keyring
    from opagent_worker import facevault

    prev = keyring.get_keyring()
    keyring.set_keyring(MemKeyring().backend)
    try:
        f.enable()
        _library(f, people=(1, 2), per=4)
        f.post("/faces/cluster")
        f.index()
        facevault.new_key("default")
        home = tmp_path / "w"
        (home / "faces").mkdir(parents=True)
        (home / "faces" / "c1.webp").write_bytes(b"crop")
        rep = f.post("/faces/purge", {"confirm": "Default"})
        assert rep["faces"] == 8 and rep["id"] and rep["at"] and len(rep["sha256"]) == 64
        # not yet verified: the worker has not destroyed its key and crops
        v = f.post("/faces/purge/verify", {"report_id": rep["id"]})
        assert v["verified"] is False and {c["id"]: c["ok"] for c in v["checks"]}["worker"] is False
        w = WorkerAgent(f.client, roots=[], home=home, worker_id="w1")
        while w.run_once():
            pass
        assert not (home / "faces").exists()
        assert keyring.get_password(facevault.SERVICE, facevault.key_name("default")) is None
        # a backup taken after the purge holds no biometric data at all
        bk = Backups(tmp_path / "backups", find_pg_bin(), FakeClock()).manual(f.dsn)
        listing = subprocess.run(
            [str(find_pg_bin() / "pg_restore"), "--list", str(bk.path)], capture_output=True, text=True, check=True
        ).stdout
        assert " bio " not in listing and "face_embedding" not in listing
        v = f.post("/faces/purge/verify", {"report_id": rep["id"]})
        assert v["verified"] is True, v
        assert {c["id"] for c in v["checks"]} == {"report_hash", "database", "bio_schema", "worker"}
        with bio_session(f.client.app.state.opa) as c:
            assert c.execute(text("select count(*) from bio.face_embedding")).scalar() == 0
        # the report cannot be quietly rewritten
        f.sql("update audit_log set details = jsonb_set(details, '{faces}', '0') where action = 'faces.purge'")
        v = f.post("/faces/purge/verify", {"report_id": rep["id"]})
        assert v["verified"] is False and {c["id"]: c["ok"] for c in v["checks"]}["report_hash"] is False
    finally:
        keyring.set_keyring(prev)
