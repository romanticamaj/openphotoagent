"""GET /onboarding/recommendation: the one-sentence hardware conclusion and the single recommended combination
(04 §8.2 W3–W5, 06 §6.6, 08 §3.3). Backs journey A (E2E-A-001..005)."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_registry.loader import BUILTIN_DIR
from opagent_worker.agent import WorkerAgent

REV = "0123456789abcdef0123456789abcdef01234567"


def _pin(model, variant, files):
    """Catalog pins only: nothing is downloaded here, so sizes can be realistic."""
    return {
        "model": model,
        "variant": variant,
        "revision": REV,
        "files": [{"path": p, "size": n, "sha256": f"{i:064x}", "lfs_oid": f"{i:064x}"} for i, (p, n) in files],
    }


@pytest.fixture
def client(embedded_pg, sandbox_tree, tmp_path):
    reg = tmp_path / "registry"
    shutil.copytree(BUILTIN_DIR, reg)
    sig = enumerate(
        [("vision_model_int8.onnx", 300_000_000), ("text_model_int8.onnx", 300_000_000), ("tokenizer.json", 1)]
    )
    qw = enumerate([("Qwen3VL-8B-Instruct-Q4_K_M.gguf", 4_000_000_000), ("mmproj-Qwen3VL-8B-Instruct-F16.gguf", 1)], 5)
    pins = [_pin("siglip2-so400m-384", "onnx-int8", sig), _pin("qwen3-vl-8b-instruct", "q4_k_m", qw)]
    (reg / "pins.lock").write_text(json.dumps({"schema_version": 1, "pins": pins}))
    c = TestClient(create_app(dsn=embedded_pg.dsn, registry_dir=reg))

    def worker(hw: str, wid: str) -> str:
        WorkerAgent(c, roots=sandbox_tree.roots, home=tmp_path / wid, worker_id=wid, hw_profile=hw).register()
        return wid

    c.worker = worker  # type: ignore[attr-defined]
    return c


def _reco(client, hw):
    wid = client.worker(hw, hw)
    r = client.get("/api/v1/onboarding/recommendation", params={"worker_id": wid})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.integration
def test_standard_mode_one_combination_with_size_time_and_licence(client):
    r = _reco(client, "rtx5070ti-win11")
    assert r["mode"] == "standard"
    assert r["hardware"]["gpu"] and r["hardware"]["vram_gb"] == pytest.approx(16, abs=0.5)
    assert r["combo"]["variants"] == ["siglip2-so400m-384@onnx-int8", "qwen3-vl-8b-instruct@q4_k_m"]  # embed first
    assert r["combo"]["download_bytes"] >= 4_600_000_000 and r["combo"]["download_minutes"] > 0
    assert r["licenses"] == ["Apache-2.0"] and r["requires_acceptance"] is False
    assert [w["code"] for w in r["warnings"]] == []


@pytest.mark.integration
def test_old_driver_offers_driver_page_and_vulkan(client):
    r = _reco(client, "nvidia-drv535")
    [w] = [w for w in r["warnings"] if w["code"] == "GPU_DRIVER_TOO_OLD"]
    types = {f["type"]: f for f in w["fixes"]}
    assert types["open_url"]["url"].startswith("https://") and types["choose_alternative"]["pack"] == "llamacpp-vulkan"
    assert "llamacpp-vulkan" in r["combo"]["packs"] and r["mode"] == "standard"


@pytest.mark.integration
def test_no_gpu_recommends_fast_mode_with_explicit_time(client):
    r = _reco(client, "no-gpu-linux")
    assert r["mode"] == "fast"
    assert r["combo"]["variants"] == ["siglip2-so400m-384@onnx-int8"]
    assert "GPU_NOT_FOUND" in [w["code"] for w in r["warnings"]]
    lo, hi = r["per_1000_minutes"]
    assert 0 < lo <= hi  # from the CPU perf prior (2–8 photos/s) → about 2–8 minutes per 1,000 photos
    assert lo == pytest.approx(1000 / 8 / 60, rel=0.01) and hi == pytest.approx(1000 / 2 / 60, rel=0.01)
