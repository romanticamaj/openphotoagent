"""Presets: export, import, use (04 §2.2; 09b §6 PRESET). An imported preset is stored only after it validated, and
an organize job can use it like a built-in one."""

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from opagent_inference.fake import FakeBackend
from opagent_worker.agent import WorkerAgent
from sqlalchemy import text

FX = sorted((Path(__file__).resolve().parents[4] / "fixtures/photos/basic").glob("*.jpg"))


@pytest.fixture
def env(embedded_pg, sandbox_tree, tmp_path):
    client = TestClient(create_app(dsn=embedded_pg.dsn), headers={"X-OPA-UI": "1"})
    worker = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w", backend=FakeBackend())
    worker.register()
    eng = engine_for(embedded_pg.dsn)
    yield client, worker, sandbox_tree, eng
    eng.dispose()


def stored(eng):
    with eng.begin() as c:
        return c.execute(text("select count(*) from preset")).scalar()


@pytest.mark.integration
@pytest.mark.tc("PRESET-001")
def test_preset_001_export_import_and_use(env):
    client, worker, tree, eng = env
    keys = {p["key"]: p for p in client.get("/api/v1/presets").json()}
    assert keys["by-date"]["source"] == "builtin"
    r = client.get("/api/v1/presets/by-date/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    doc = yaml.safe_load(r.text)
    assert doc["schema_version"] == 2 and doc["kind"] == "opagent-preset" and doc["preset"]["key"] == "by-date"
    doc["preset"]["key"] = "trips-by-year"
    doc["preset"]["output_rules"][0]["path_template"] = "{taken_at:%Y}/{stem}{ext}"
    doc["preset"]["shared_note"] = "from a newer version"  # unknown here: kept
    r = client.post("/api/v1/presets/import", json={"text": yaml.safe_dump(doc, allow_unicode=True)})
    assert r.status_code == 201, r.text
    assert r.json()["key"] == "trips-by-year" and r.json()["migrated_from"] is None
    again = yaml.safe_load(client.get("/api/v1/presets/trips-by-year/export").text)
    assert again["preset"]["shared_note"] == "from a newer version"
    assert {p["key"]: p for p in client.get("/api/v1/presets").json()}["trips-by-year"]["source"] == "imported"

    # an old bare (v1) file migrates
    v1 = (Path(__file__).resolve().parents[4] / "apps/api/src/opagent_api/presets/builtin/by-date.yaml").read_text(
        encoding="utf-8"
    )
    r = client.post("/api/v1/presets/import", json={"text": v1.replace("key: by-date", "key: old-style")})
    assert r.status_code == 201 and r.json()["migrated_from"] == 1

    # and an organize job can use the imported preset
    (tree.photos / "trip").mkdir()
    for i in range(2):
        shutil.copyfile(FX[i], tree.photos / f"trip/p{i}.jpg")
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": "trip"}).json()
    job = client.post(
        "/api/v1/jobs",
        json={"source_id": src["id"], "preset": "trips-by-year", "target": {"root_alias": "sorted", "sub_path": ""}},
    )
    assert job.status_code == 201, job.text
    worker.run_until_idle()
    j = client.get(f"/api/v1/jobs/{job.json()['id']}").json()
    assert j["status"] == "awaiting_approval"
    plan = client.get(f"/api/v1/output-plans/{j['plan_id']}").json()
    assert all(a["dst_rel"].count("/") == 1 for a in plan["actions"])  # {taken_at:%Y}/{stem}{ext}


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("PRESET-002")
def test_preset_002_malicious_import_refused_nothing_stored(env):
    client, worker, tree, eng = env
    base = yaml.safe_load(client.get("/api/v1/presets/by-date/export").text)
    base["preset"]["key"] = "evil"
    bad_tpl = yaml.safe_load(yaml.safe_dump(base))
    bad_tpl["preset"]["output_rules"][0]["path_template"] = "../../{stem}"
    bad_dsl = yaml.safe_load(yaml.safe_dump(base))
    bad_dsl["preset"]["output_rules"][0]["when"] = "__import__('os').system('rm -rf /')"
    for doc, reason in ((bad_tpl, "TEMPLATE_PATH_ESCAPE"), (bad_dsl, "RULE_FORBIDDEN")):
        r = client.post("/api/v1/presets/import", json={"text": yaml.safe_dump(doc)})
        assert r.status_code == 422, r.text
        body = r.json()
        assert body["code"] == "PRESET_INVALID" and body["params"]["reason"] == reason
        assert body["params"]["pointer"].startswith("/preset/output_rules/0/")
    r = client.post("/api/v1/presets/import", json={"text": "a: &x [1]\nb: [*x, *x]\n"})
    assert r.status_code == 422 and r.json()["code"] == "PRESET_INVALID"
    assert stored(eng) == 0
    clash = yaml.safe_load(client.get("/api/v1/presets/by-date/export").text)
    r = client.post("/api/v1/presets/import", json={"text": yaml.safe_dump(clash)})
    assert r.status_code == 409 and r.json()["code"] == "PRESET_KEY_TAKEN"
    assert r.json()["fixes"][0]["target"] == "import_renamed"
    r = client.post("/api/v1/presets/import", json={"text": yaml.safe_dump(clash), "rename": True})
    assert r.status_code == 201 and r.json()["key"] == "by-date-2"
    assert client.post("/api/v1/presets/import", json={"text": "x"}, headers={"X-OPA-UI": ""}).status_code == 403
