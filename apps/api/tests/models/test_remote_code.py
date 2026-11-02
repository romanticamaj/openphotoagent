"""RT-TORCH-003 (09b §7.1; invariant 10): a model with remote code loads only after the user confirmed the exact
files; the confirmation is bound to their hashes, so new code needs a new confirmation."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_registry.loader import BUILTIN_DIR

ROOT = Path(__file__).resolve().parents[4]
KEY = "fixture-remote-code@fp16"
SHA = {"model.safetensors": "a" * 64, "modeling_florence2.py": "b" * 64, "processing_florence2.py": "c" * 64}


def pins(reg, sha):
    files = [{"path": p, "size": 100, "sha256": h, "lfs_oid": h} for p, h in sha.items()]
    rev = "0123456789abcdef0123456789abcdef01234567"
    (reg / "pins.lock").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pins": [{"model": "fixture-remote-code", "variant": "fp16", "revision": rev, "files": files}],
            }
        )
    )


@pytest.fixture
def env(embedded_pg, tmp_path):
    reg = tmp_path / "registry"
    shutil.copytree(BUILTIN_DIR, reg)
    shutil.copy(ROOT / "fixtures/registry/models/fixture-remote-code.yaml", reg / "models")
    pins(reg, SHA)
    c = TestClient(create_app(dsn=embedded_pg.dsn, registry_dir=reg), headers={"X-OPA-UI": "1"})
    r = c.post("/worker-api/v1/register", json={"worker_id": "w1", "roots": [], "hw": {}})
    assert r.status_code == 200, r.text
    return c, reg


def blockers(c):
    pf = c.post("/api/v1/preflight", json={"action": "model.load", "params": {"variant": KEY}}).json()
    return {b["code"]: b for b in pf["blockers"]}


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("RT-TORCH-003")
def test_rt_torch_003_remote_code_needs_a_confirmation_bound_to_hashes(env):
    c, reg = env
    b = blockers(c)["MODEL_REMOTE_CODE_UNCONFIRMED"]
    assert b["params"]["files"] == ["modeling_florence2.py", "processing_florence2.py"]
    assert b["fixes"][0]["target"] == "confirm_remote_code"
    info = c.get(f"/api/v1/models/{KEY}/remote-code").json()
    assert info == {
        "files": [
            {"path": "modeling_florence2.py", "sha256": "b" * 64},
            {"path": "processing_florence2.py", "sha256": "c" * 64},
        ],
        "confirmed": False,
    }
    wrong = {"modeling_florence2.py": "b" * 64}  # must name every file it will run
    assert c.post(f"/api/v1/models/{KEY}/remote-code/confirm", json={"files": wrong}).status_code == 409
    ok = {"modeling_florence2.py": "b" * 64, "processing_florence2.py": "c" * 64}
    r = c.post(f"/api/v1/models/{KEY}/remote-code/confirm", json={"files": ok})
    assert r.status_code == 200 and r.json()["confirmed_at"]
    assert "MODEL_REMOTE_CODE_UNCONFIRMED" not in blockers(c)
    assert (
        c.post(f"/api/v1/models/{KEY}/remote-code/confirm", json={"files": ok}, headers={"X-OPA-UI": ""}).status_code
        == 403
    )
    pins(reg, {**SHA, "modeling_florence2.py": "d" * 64})  # a new revision changed the code
    assert "MODEL_REMOTE_CODE_UNCONFIRMED" in blockers(c)
