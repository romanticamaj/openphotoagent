"""Immich write-back (02 §8.2; 09b §7.3 IMM-001..005): plan → approve → journal → apply, undo removes only what we
added, assets are matched by SHA-1, the key never leaves the keyring, and a second apply writes nothing."""

import base64
import hashlib
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from opagent_testing.immich_server import FakeImmich
from opagent_worker.agent import WorkerAgent
from sqlalchemy import text

FX = sorted((Path(__file__).resolve().parents[4] / "fixtures/photos/basic").glob("*.jpg"))[:3]


def sha1_b64(p: Path) -> str:
    return base64.b64encode(hashlib.sha1(p.read_bytes()).digest()).decode()


@pytest.fixture
def env(embedded_pg, sandbox_tree, tmp_path, monkeypatch):
    monkeypatch.delenv("OPAGENT_TOKEN", raising=False)
    imm = FakeImmich().start()
    (sandbox_tree.photos / "lib").mkdir()
    for f in FX:
        shutil.copyfile(f, sandbox_tree.photos / "lib" / f.name)
    c = TestClient(create_app(dsn=embedded_pg.dsn), headers={"X-OPA-UI": "1"})
    c.app.state.opa.home = tmp_path / "home"
    w = WorkerAgent(c, roots=sandbox_tree.roots, home=tmp_path / "w")
    w.register()
    src = c.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "lib"}).json()
    c.post(f"/api/v1/sources/{src['id']}/scan")
    w.run_until_idle()
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as x:
        rows = x.execute(text("select asset_id, rel_path from asset_location order by rel_path")).all()
        ids = {Path(r.rel_path).name: r.asset_id for r in rows}
        for name, aid in ids.items():
            x.execute(
                text("update search_doc set tags = :t, caption = :c where asset_id = :a"),
                {"t": ["animal.cat", "scene.beach"], "c": f"海邊的貓 {name}", "a": aid},
            )
    # two of the three photos are in Immich; the first already carries a tag of its own
    first = imm.add_asset(sha1_b64(sandbox_tree.photos / "lib" / FX[0].name), description="", tags=("家人",))
    second = imm.add_asset(sha1_b64(sandbox_tree.photos / "lib" / FX[1].name), description="我寫的描述")
    yield {"c": c, "w": w, "imm": imm, "ids": ids, "im": (first, second), "eng": eng}
    eng.dispose()
    imm.stop()


def connect(e, key="immich-key"):
    r = e["c"].put("/api/v1/integrations/immich", json={"url": e["imm"].url, "api_key": key})
    assert r.status_code == 200, r.text
    return r.json()


def plan(e, **opts):
    body = {"asset_ids": sorted(e["ids"].values()), "tags": True, **opts}
    r = e["c"].post("/api/v1/integrations/immich/plans", json=body)
    assert r.status_code == 201, r.text
    e["w"].run_until_idle()  # SHA-1 of each photo is computed by the worker (invariant 1)
    return e["c"].get(f"/api/v1/integrations/immich/plans/{r.json()['id']}").json()


@pytest.mark.integration
@pytest.mark.tc("IMM-001", "IMM-002")
def test_imm_001_002_tags_by_checksum_apply_and_undo(env):
    e = env
    connect(e)
    p = plan(e)
    assert p["status"] == "draft" and p["summary"] == {"matched": 2, "skipped": 1, "actions": 2}
    skipped = p["skipped"]
    assert skipped == [{"asset_id": e["ids"][FX[2].name], "reason": "NOT_IN_IMMICH"}]
    assert e["imm"].writes == 0  # nothing written before approval
    r = e["c"].post(f"/api/v1/integrations/immich/plans/{p['id']}/apply")
    assert r.status_code == 200 and r.json()["status"] == "applied"
    first, second = e["im"]
    want = {"OpenPhotoAgent/貓", "OpenPhotoAgent/海灘"}
    assert e["imm"].tag_values(first) == {"家人"} | want and e["imm"].tag_values(second) == want
    with e["eng"].begin() as x:
        j = x.execute(text("select state, count(*) from integration_journal group by state")).all()
    assert dict(j) == {"done": len(want)}  # one journal entry per tag write
    r = e["c"].post(f"/api/v1/integrations/immich/plans/{p['id']}/undo")
    assert r.status_code == 200 and r.json()["status"] == "undone"
    assert e["imm"].tag_values(first) == {"家人"} and e["imm"].tag_values(second) == set()  # only ours removed


@pytest.mark.integration
@pytest.mark.tc("IMM-003")
def test_imm_003_album_and_description(env):
    e = env
    connect(e)
    p = plan(e, tags=False, album="2025 海邊", description=True)
    e["c"].post(f"/api/v1/integrations/immich/plans/{p['id']}/apply")
    first, second = e["im"]
    album = next(a for a in e["imm"].albums.values() if a["albumName"] == "2025 海邊")
    assert album["assets"] == {first, second}
    assert e["imm"].assets[first]["description"].startswith("海邊的貓")  # was empty: filled
    assert e["imm"].assets[second]["description"] == "我寫的描述"  # not overwritten by default
    p2 = plan(e, tags=False, description=True, overwrite_description=True)
    e["c"].post(f"/api/v1/integrations/immich/plans/{p2['id']}/apply")
    assert e["imm"].assets[second]["description"].startswith("海邊的貓")


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("IMM-004")
def test_imm_004_key_in_keyring_and_preflight(env):
    e = env
    state = connect(e)
    assert state == {"url": e["imm"].url, "has_api_key": True}
    assert "immich-key" not in e["c"].get("/api/v1/integrations/immich").text
    with e["eng"].begin() as x:
        assert "immich-key" not in str(x.execute(text("select settings from workspace")).scalar())
    pf = e["c"].post("/api/v1/preflight", json={"action": "immich.sync", "params": {}}).json()
    assert pf["ready"] is True
    connect(e, key="wrong")
    pf = e["c"].post("/api/v1/preflight", json={"action": "immich.sync", "params": {}}).json()
    assert [b["code"] for b in pf["blockers"]] == ["IMMICH_AUTH_FAILED"] and pf["blockers"][0]["fixes"]
    e["imm"].stop()
    pf = e["c"].post("/api/v1/preflight", json={"action": "immich.sync", "params": {}}).json()
    assert [b["code"] for b in pf["blockers"]] == ["IMMICH_UNREACHABLE"] and pf["blockers"][0]["fixes"]


@pytest.mark.integration
@pytest.mark.tc("IMM-005")
def test_imm_005_second_apply_writes_nothing(env):
    e = env
    connect(e)
    p = plan(e, album="同一本")
    e["c"].post(f"/api/v1/integrations/immich/plans/{p['id']}/apply")
    before = e["imm"].writes
    p2 = plan(e, album="同一本")
    assert p2["summary"]["actions"] == 0  # everything already in place
    e["c"].post(f"/api/v1/integrations/immich/plans/{p2['id']}/apply")
    assert e["imm"].writes == before
