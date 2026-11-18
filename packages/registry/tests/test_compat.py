import json
import shutil
from pathlib import Path

import pytest
from opagent_doctor.probes import load_profile
from opagent_registry.compat import CompatCache, compat, compat_inputs_hash
from opagent_registry.loader import BUILTIN_DIR, load_registry

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "fixtures" / "golden" / "compat_matrix.json"
PROFILES = ["rtx5070ti-win11", "rtx4060-laptop", "m3-16", "no-gpu-linux", "amd-rx7800-win", "intel-mac"]


def reg_with_fixtures(tmp_path):
    d = tmp_path / "registry"
    shutil.copytree(BUILTIN_DIR, d)
    shutil.copy(ROOT / "fixtures" / "registry" / "models" / "fixture-nc-vlm.yaml", d / "models")
    return load_registry(d)


def matrix(reg):
    out = {}
    for p in PROFILES:
        hw = load_profile(p)
        for m in sorted(reg.models):
            if reg.models[m].spec.kind.startswith("face_"):  # MDL-002 is the 8 VLM/embedding variants; faces: 07
                continue
            for v in reg.models[m].spec.variants:
                c = compat(reg.models[m], v.id, hw, reg.runtimes, gpu_classes=reg.gpu_classes)
                out[f"{p}|{m}@{v.id}"] = {
                    "level": c.level,
                    "reasons": [r["code"] for r in c.reasons],
                    "prep": [x["code"] for x in c.prep],
                }
    return out


@pytest.mark.unit
@pytest.mark.tc("MDL-001")
def test_mdl_001_platform_first_but_prep_collected(tmp_path):
    reg = reg_with_fixtures(tmp_path)
    c = compat(reg.models["fixture-nc-vlm"], "q4", load_profile("m3-16"), reg.runtimes)  # os excludes macos
    assert c.level == "blocked" and [r["code"] for r in c.reasons] == ["RUNTIME_UNSUPPORTED_PLATFORM"]
    assert c.vram == {}  # later hard checks were not evaluated
    assert [p["code"] for p in c.prep] == ["MODEL_NOT_INSTALLED", "LICENSE_CONSENT_REQUIRED"]


@pytest.mark.unit
@pytest.mark.tc("MDL-002")
def test_mdl_002_golden_matrix(tmp_path):
    reg = reg_with_fixtures(tmp_path)
    got = matrix(reg)
    assert len(got) == 54  # 9 VLM/embedding variants x 6 profiles (qwen3-embedding added 2026-11-10, reviewed)
    assert got == json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.unit
@pytest.mark.tc("MDL-003")
def test_mdl_003_prep_never_changes_level(tmp_path):
    reg = reg_with_fixtures(tmp_path)
    hw = load_profile("rtx5070ti-win11")
    m = reg.models["fixture-nc-vlm"]
    bare = compat(m, "q4", hw, reg.runtimes)
    ready = compat(
        m,
        "q4",
        hw.with_packs(("llamacpp-cuda13",)),
        reg.runtimes,
        installed=["fixture-nc-vlm@q4"],
        consents=["fixture-nc-vlm"],
    )
    assert bare.level == ready.level == "ok"
    assert [p["code"] for p in bare.prep] == ["RUNTIME_PACK_MISSING", "MODEL_NOT_INSTALLED", "LICENSE_CONSENT_REQUIRED"]
    assert ready.prep == []


@pytest.mark.unit
@pytest.mark.tc("MDL-004")
def test_mdl_004_cache_invalidation():
    reg = load_registry()
    hw = load_profile("rtx5070ti-win11")
    calls = []
    cache = CompatCache(compute=lambda *a, **k: calls.append(1) or compat(*a, **k))
    m = reg.models["qwen3-vl-8b-instruct"]
    cache.get(m, "q5_k_m", hw, reg)
    cache.get(m, "q5_k_m", hw, reg)
    assert len(calls) == 1  # hit
    cache.get(m, "q5_k_m", hw.with_gpu_driver("590.00"), reg)  # hardware digest changed
    cache.get(m, "q5_k_m", hw.with_packs(("llamacpp-cuda13",)), reg)  # pack installed
    reg2 = load_registry()
    reg2.version = "other"
    cache.get(m, "q5_k_m", hw, reg2)  # registry version changed
    assert len(calls) == 4
    busy = load_profile("game-running-5gb")
    assert compat_inputs_hash(hw, reg) != compat_inputs_hash(hw.with_packs(("x",)), reg)
    assert busy.digest() == load_profile("game-running-5gb").digest()
