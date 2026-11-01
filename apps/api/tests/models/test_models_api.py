import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from opagent_registry.loader import BUILTIN_DIR
from opagent_testing.hfserver import HfFile, HfServer
from opagent_worker.agent import WorkerAgent
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[4]
EMB = "siglip2-so400m-384"
INT8 = ["vision_model_int8.onnx", "text_model_int8.onnx", "tokenizer.json"]


@pytest.fixture
def env(embedded_pg, sandbox_tree, tmp_path):
    """Registry copy with fixture pins for SigLIP2 int8 whose bytes the fake HF server serves (ADR-0002)."""
    files = [HfFile(p, 200_000 + i * 1000, seed=i) for i, p in enumerate(INT8)]
    srv = HfServer("openphotoagent/siglip2-so400m-patch14-384-onnx", files).start()
    reg = tmp_path / "registry"
    shutil.copytree(BUILTIN_DIR, reg)
    shutil.copy(ROOT / "fixtures/registry/models/fixture-nc-vlm.yaml", reg / "models")
    pins = [
        {
            "model": EMB,
            "variant": "onnx-int8",
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "files": [{"path": f.path, "size": f.size, "sha256": f.sha256, "lfs_oid": f.sha256} for f in files],
        }
    ]
    (reg / "pins.lock").write_text(json.dumps({"schema_version": 1, "pins": pins}))
    user = tmp_path / "custom-models"
    user.mkdir()
    client = TestClient(create_app(dsn=embedded_pg.dsn, registry_dir=reg, user_models_dir=user))
    w = WorkerAgent(
        client,
        roots=sandbox_tree.roots,
        home=tmp_path / "w",
        worker_id="w1",
        hw_profile="rtx5070ti-win11",
        models_root=tmp_path / "models",
        download_sources=[srv.url],
    )
    w.register()
    yield {"client": client, "worker": w, "srv": srv, "user": user, "tmp": tmp_path, "dsn": embedded_pg.dsn}
    srv.stop()


def _drain(w):
    for _ in range(50):
        if not w.run_once():
            return


def _models(client, **params):
    return {m["id"]: m for m in client.get("/api/v1/models", params={"worker_id": "w1", **params}).json()["items"]}


def _events(client):
    with client.stream("GET", "/api/v1/events", params={"once": 1}) as s:
        return "".join(s.iter_text())


@pytest.mark.contract
@pytest.mark.tc("MDL-012")
def test_mdl_012_list_models_has_compat_and_legacy_fits_now(env):
    m = _models(env["client"])["qwen3-vl-8b-instruct"]
    v = next(x for x in m["variants"] if x["id"] == "q5_k_m")
    assert v["compat"]["level"] == "ok" and v["compat"]["label_key"] == "compat.ok"
    assert {"reasons", "prep", "vram", "fits_now"} <= set(v["compat"])
    assert "fits_now" in v and v["fits_now"] == v["compat"]["fits_now"]
    assert m["level"] == "ok" and m["best_variant"] in {x["id"] for x in m["variants"]}
    assert v["pin_state"] == "unpinned"


@pytest.mark.integration
@pytest.mark.tc("INF-REG-003")
def test_inf_reg_003_user_yaml_shows_up(env):
    (env["user"] / "mine.yaml").write_text(
        (ROOT / "fixtures/registry/models/fixture-nc-vlm.yaml")
        .read_text()
        .replace("fixture-nc-vlm", "my-user-vlm")
        .replace('"nc-q4.gguf" }', '"nc-q4.gguf", size: 10, sha256: "' + "d" * 64 + '" }')
    )
    m = _models(env["client"])["my-user-vlm"]
    assert m["source"] == "user" and m["status"] == "valid"
    assert m["variants"][0]["compat"]["level"] in ("ok", "degraded", "blocked")


@pytest.mark.integration
@pytest.mark.tc("DL-002")
def test_dl_002_install_flow_writes_installation_and_event(env):
    c, w = env["client"], env["worker"]
    r = c.post("/api/v1/workers/w1/installs", json={"variant": f"{EMB}@onnx-int8"})
    assert r.status_code == 202, r.text
    op = r.json()["operation_id"]
    _drain(w)
    o = c.get(f"/api/v1/operations/{op}").json()
    assert o["type"] == "model.download" and o["status"] == "succeeded"
    v = next(x for x in _models(c)[EMB]["variants"] if x["id"] == "onnx-int8")
    assert v["installation"]["status"] == "installed"
    assert "MODEL_NOT_INSTALLED" not in [p["code"] for p in v["compat"]["prep"]]
    assert "event: model.installed" in _events(c)


@pytest.mark.integration
@pytest.mark.tc("DL-002")
def test_install_unpinned_builtin_is_blocked(env):
    r = env["client"].post("/api/v1/workers/w1/installs", json={"variant": "qwen3-vl-8b-instruct@q5_k_m"})
    assert r.status_code == 409 and r.json()["code"] == "PREFLIGHT_BLOCKED"
    b = r.json()["blockers"][0]
    assert b["code"] == "MODEL_NOT_PINNED" and [f["type"] for f in b["fixes"]] == ["open_docs", "choose_alternative"]


@pytest.mark.integration
@pytest.mark.tc("MDL-007")
def test_mdl_007_delete_in_use_then_after_job(env):
    c, w = env["client"], env["worker"]
    c.post("/api/v1/workers/w1/installs", json={"variant": f"{EMB}@onnx-int8"})
    _drain(w)
    inst = next(x for x in _models(c)[EMB]["variants"] if x["id"] == "onnx-int8")["installation"]
    eng = engine_for(env["dsn"])
    with eng.begin() as conn:  # a running job depends on the model
        conn.execute(
            text(
                "insert into job(id, workspace_id, kind, status, params) values ('jx','default','organize','running', :p)"
            ),
            {"p": json.dumps({"models": [f"{EMB}@onnx-int8"]})},
        )
    r = c.delete(f"/api/v1/workers/w1/installations/{inst['id']}")
    assert r.status_code == 409 and r.json()["blockers"][0]["code"] == "MODEL_IN_USE"
    assert [(f["type"], f["id"]) for f in r.json()["blockers"][0]["fixes"]] == [("retry", "after_jobs")]  # PF-044
    with eng.begin() as conn:
        conn.execute(text("update job set status='completed' where id='jx'"))
    eng.dispose()
    r = c.delete(f"/api/v1/workers/w1/installations/{inst['id']}")
    assert r.status_code == 202
    _drain(w)
    assert r.json()["freed_bytes"] == sum(200_000 + i * 1000 for i in range(3))
    assert not list((env["tmp"] / "models" / "hf").rglob("*.onnx"))
    assert "event: model.removed" in _events(c)
    assert next(x for x in _models(c)[EMB]["variants"] if x["id"] == "onnx-int8")["installation"] is None


@pytest.mark.integration
@pytest.mark.tc("MDL-011")
def test_mdl_011_license_consent(env):
    c = env["client"]
    ms = _models(c)
    assert "LICENSE_CONSENT_REQUIRED" not in [p["code"] for v in ms[EMB]["variants"] for p in v["compat"]["prep"]]
    nc = ms["fixture-nc-vlm"]
    assert "LICENSE_CONSENT_REQUIRED" in [p["code"] for p in nc["variants"][0]["compat"]["prep"]]
    lic = c.get("/api/v1/licenses/fixture-nc-vlm").json()
    assert lic["requires_acceptance"] and lic["license_hash"]
    r = c.post("/api/v1/licenses/fixture-nc-vlm/accept", json={"license_hash": lic["license_hash"]})
    assert r.status_code == 200
    nc = _models(c)["fixture-nc-vlm"]
    assert "LICENSE_CONSENT_REQUIRED" not in [p["code"] for p in nc["variants"][0]["compat"]["prep"]]
    eng = engine_for(env["dsn"])
    with eng.begin() as conn:
        row = conn.execute(text("select license_hash from license_acceptance where model_id='fixture-nc-vlm'")).one()
        assert row.license_hash == lic["license_hash"]
        conn.execute(text("update license_acceptance set license_hash='old' where model_id='fixture-nc-vlm'"))
    eng.dispose()
    nc = _models(c)["fixture-nc-vlm"]  # licence text changed: consent no longer matches
    assert "LICENSE_CONSENT_REQUIRED" in [p["code"] for p in nc["variants"][0]["compat"]["prep"]]
    assert c.post("/api/v1/licenses/fixture-nc-vlm/accept", json={"license_hash": "stale"}).status_code == 409


@pytest.mark.integration
@pytest.mark.tc("INF-VRAM-005")
def test_inf_vram_005_observed_vram_used_next_time(env):
    c = env["client"]
    v = next(x for x in _models(c)["qwen3-vl-8b-instruct"]["variants"] if x["id"] == "q5_k_m")
    assert v["compat"]["vram"]["need_gb"] == 8.5 and v["compat"]["vram"]["source"] == "estimated"
    r = c.post(
        "/worker-api/v1/observations",
        json={"worker_id": "w1", "kind": "vram", "key": "qwen3-vl-8b-instruct@q5_k_m", "value": 9.1},
    )
    assert r.status_code == 200
    v = next(x for x in _models(c)["qwen3-vl-8b-instruct"]["variants"] if x["id"] == "q5_k_m")
    assert v["compat"]["vram"]["need_gb"] == 9.1 and v["compat"]["vram"]["source"] == "observed"


@pytest.mark.integration
@pytest.mark.tc("DL-020")
def test_dl_020_onboarding_downloads_siglip_first_then_index(env):
    c, w = env["client"], env["worker"]
    r = c.post(
        "/api/v1/onboarding/install",
        json={"worker_id": "w1", "variants": ["qwen3-vl-8b-instruct@q5_k_m", f"{EMB}@onnx-int8"]},
    )
    assert r.status_code == 202
    plan = r.json()
    assert plan["order"][0] == f"{EMB}@onnx-int8"
    assert plan["blocked"] == [{"variant": "qwen3-vl-8b-instruct@q5_k_m", "code": "MODEL_NOT_PINNED"}]
    _drain(w)
    ops = c.get("/api/v1/operations", params={"status": "all", "type": "search.reindex"}).json()
    assert ops and ops[0]["status"] in ("queued", "running", "succeeded")
