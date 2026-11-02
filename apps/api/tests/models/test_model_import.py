"""MDL-IMP-001/002 (09b §7.2; 06 §1.8): register models that are already on disk — Ollama's store or a folder —
by hash, without downloading or copying them. Pickle weights are refused (invariant 10)."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_registry.loader import BUILTIN_DIR
from opagent_worker.agent import WorkerAgent

KEY = "qwen3-vl-2b-instruct@q4_k_m"
W, P = b"GGUF-weights-" * 1000, b"GGUF-projector-" * 500


def sha(b):
    return hashlib.sha256(b).hexdigest()


def ollama_store(root: Path) -> Path:
    blobs = root / "blobs"
    blobs.mkdir(parents=True)
    for data in (W, P):
        (blobs / f"sha256-{sha(data)}").write_bytes(data)
    man = root / "manifests/registry.ollama.ai/library/qwen3-vl/2b"
    man.parent.mkdir(parents=True)
    man.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "layers": [
                    {"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{sha(W)}", "size": len(W)},
                    {
                        "mediaType": "application/vnd.ollama.image.projector",
                        "digest": f"sha256:{sha(P)}",
                        "size": len(P),
                    },
                    {"mediaType": "application/vnd.ollama.image.template", "digest": "sha256:" + "0" * 64, "size": 3},
                ],
            }
        )
    )
    return root


@pytest.fixture
def env(embedded_pg, sandbox_tree, tmp_path):
    reg = tmp_path / "registry"
    shutil.copytree(BUILTIN_DIR, reg)
    rev = "0123456789abcdef0123456789abcdef01234567"
    files = [
        {"path": "Qwen3VL-2B-Instruct-Q4_K_M.gguf", "size": len(W), "sha256": sha(W), "lfs_oid": sha(W)},
        {"path": "mmproj-Qwen3VL-2B-Instruct-F16.gguf", "size": len(P), "sha256": sha(P), "lfs_oid": sha(P)},
    ]
    (reg / "pins.lock").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pins": [{"model": "qwen3-vl-2b-instruct", "variant": "q4_k_m", "revision": rev, "files": files}],
            }
        )
    )
    c = TestClient(create_app(dsn=embedded_pg.dsn, registry_dir=reg), headers={"X-OPA-UI": "1"})
    w = WorkerAgent(
        c,
        roots=sandbox_tree.roots,
        home=tmp_path / "w",
        worker_id="w1",
        hw_profile="rtx5070ti-win11",
        models_root=tmp_path / "models",
        download_sources=["http://127.0.0.1:9/never"],
    )
    w.ollama_dir = ollama_store(tmp_path / "ollama")
    w.register()
    return c, w, tmp_path


def scan(c, w, **body):
    r = c.post("/api/v1/models/import/scan", json=body)
    assert r.status_code == 202, r.text
    while w.run_once():
        pass
    return c.get(f"/api/v1/models/import/scan/{r.json()['scan_id']}").json()


@pytest.mark.integration
@pytest.mark.tc("MDL-IMP-001")
def test_mdl_imp_001_ollama_model_registered_without_download(env):
    c, w, tmp = env
    res = scan(c, w, source="ollama")
    cand = next(x for x in res["candidates"] if x["name"] == "qwen3-vl:2b")
    assert cand["match"] == KEY and cand["format"] == "gguf" and cand["mmproj"]["sha256"] == sha(P)
    r = c.post("/api/v1/models/import", json={"scan_id": res["id"], "candidate": cand["id"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"key": KEY, "status": "installed", "downloaded_bytes": 0, "managed": False}
    inst = {m["key"]: m for m in c.get("/api/v1/workers/w1/installations").json()}
    assert inst[KEY]["status"] == "installed" and inst[KEY]["managed"] is False
    roles = {f["role"]: f["path"] for f in inst[KEY]["files"]}
    assert roles["weights"].endswith(f"sha256-{sha(W)}") and roles["mmproj"].endswith(f"sha256-{sha(P)}")
    assert not (tmp / "models").exists() or not any((tmp / "models").rglob("*.gguf"))  # nothing copied


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MDL-IMP-002")
def test_mdl_imp_002_folder_import_refuses_pickle(env, tmp_path):
    c, w, tmp = env
    d = tmp_path / "my-models"
    d.mkdir()
    (d / "Qwen3VL-2B-Instruct-Q4_K_M.gguf").write_bytes(W)
    (d / "mmproj-Qwen3VL-2B-Instruct-F16.gguf").write_bytes(P)
    (d / "clip.safetensors").write_bytes(b"\x08\x00\x00\x00\x00\x00\x00\x00{}      ")
    (d / "face.onnx").write_bytes(b"onnx")
    (d / "pytorch_model.bin").write_bytes(b"\x80\x02pickle")
    res = scan(c, w, source="folder", path=str(d))
    fmts = sorted(x["format"] for x in res["candidates"])
    assert fmts == ["gguf", "onnx", "safetensors"]  # the two GGUF files are one model (weights + mmproj)
    assert res["rejected"] == [{"path": "pytorch_model.bin", "reason": "PICKLE_REJECTED"}]
    gguf = next(x for x in res["candidates"] if x["format"] == "gguf")
    assert gguf["match"] == KEY
    assert (
        c.post(
            "/api/v1/models/import/scan", json={"source": "folder", "path": str(d)}, headers={"X-OPA-UI": ""}
        ).status_code
        == 403
    )
