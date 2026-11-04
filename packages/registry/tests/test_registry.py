import json
import re
import shutil
from pathlib import Path

import pytest
from opagent_registry.defaults import resolve_task_defaults
from opagent_registry.loader import BUILTIN_DIR, RegistryLoadError, load_registry

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _copy_builtin(tmp_path: Path) -> Path:
    d = tmp_path / "registry"
    shutil.copytree(BUILTIN_DIR, d)
    return d


def _lock(d: Path, pins: list) -> None:
    (d / "pins.lock").write_text(json.dumps({"schema_version": 1, "pins": pins}), encoding="utf-8")


def _pin(model: str, variant: str, files: list[str], sha: str = "a" * 64) -> dict:
    return {
        "model": model,
        "variant": variant,
        "revision": "0123456789abcdef0123456789abcdef01234567",
        "files": [{"path": f, "size": 1000 + i, "sha256": sha, "lfs_oid": sha} for i, f in enumerate(files)],
    }


@pytest.mark.contract
@pytest.mark.tc("INF-REG-001")
def test_inf_reg_001_builtin_registry_valid():
    reg = load_registry()  # raises on any schema / lock error
    assert {"qwen3-vl-8b-instruct", "siglip2-so400m-384"} <= set(reg.models)
    assert {"llamacpp-cuda13", "llamacpp-vulkan", "ort-directml"} <= set(reg.runtimes)
    assert reg.gpu_classes
    for m in reg.models.values():
        assert m.source == "builtin" and m.status == "valid"
        for v in m.spec.variants:
            for f in v.files:  # catalog is the human-written side: never carries hashes (ADR-0002)
                assert f.size is None and f.sha256 is None
            if m.pin_state[v.id] == "pinned":
                for f in m.pinned_files(v.id):
                    assert f.size and f.size > 0 and HEX64.match(f.sha256 or "")


@pytest.mark.contract
@pytest.mark.tc("INF-REG-001")
def test_inf_reg_001_catalog_with_hash_rejected(tmp_path):
    d = _copy_builtin(tmp_path)
    p = d / "models" / "qwen3-vl-4b-instruct.yaml"
    p.write_text(
        p.read_text(encoding="utf-8").replace("role: weights }", 'role: weights, sha256: "' + "b" * 64 + '" }', 1),
        encoding="utf-8",
    )
    with pytest.raises(RegistryLoadError, match="sha256"):
        load_registry(d)


@pytest.mark.contract
@pytest.mark.tc("INF-REG-001")
def test_inf_reg_001_lock_entry_must_point_to_existing_variant_and_file(tmp_path):
    d = _copy_builtin(tmp_path)
    _lock(d, [_pin("qwen3-vl-4b-instruct", "nope", ["x.gguf"])])
    with pytest.raises(RegistryLoadError, match="nope"):
        load_registry(d)
    _lock(d, [_pin("qwen3-vl-4b-instruct", "q4_k_m", ["Qwen3VL-4B-Instruct-Q4_K_M.gguf", "missing.gguf"])])
    with pytest.raises(RegistryLoadError, match=r"missing\.gguf"):
        load_registry(d)


@pytest.mark.unit
@pytest.mark.tc("INF-REG-002")
def test_inf_reg_002_pin_states(tmp_path):
    d = _copy_builtin(tmp_path)
    files = ["Qwen3VL-4B-Instruct-Q4_K_M.gguf", "mmproj-Qwen3VL-4B-Instruct-F16.gguf"]
    _lock(d, [_pin("qwen3-vl-4b-instruct", "q4_k_m", files)])
    reg = load_registry(d)
    assert reg.models["qwen3-vl-4b-instruct"].pin_state["q4_k_m"] == "pinned"
    assert reg.models["qwen3-vl-8b-instruct"].pin_state["q4_k_m"] == "unpinned"  # no entry: not an error
    # partially pinned (one file missing from the entry) is a load error, not "pinned"
    _lock(d, [_pin("qwen3-vl-4b-instruct", "q4_k_m", files[:1])])
    with pytest.raises(RegistryLoadError, match="partial"):
        load_registry(d)
    # malformed / missing sha256 in a built-in lock entry → CI failure
    _lock(d, [_pin("qwen3-vl-4b-instruct", "q4_k_m", files, sha="XYZ")])
    with pytest.raises(RegistryLoadError):
        load_registry(d)
    bad = _pin("qwen3-vl-4b-instruct", "q4_k_m", files)
    del bad["files"][0]["sha256"]
    _lock(d, [bad])
    with pytest.raises(RegistryLoadError):
        load_registry(d)


USER_OK = """
schema_version: 1
id: my-gguf
display_name: My GGUF
family: qwen3-vl
kind: vlm
capabilities: [caption]
license: { spdx: Apache-2.0, commercial_use: true }
source: { hf_repo: someone/my-gguf }
structured_output: json_schema
variants:
  - id: q4
    runtime: llama.cpp
    files:
      - { repo: someone/my-gguf, path: m.gguf, size: 123, sha256: "%s" }
    vram_gb: { weights: 3.0 }
""" % ("c" * 64)


@pytest.mark.unit
@pytest.mark.tc("INF-REG-002")
def test_inf_reg_002_user_file_without_sha_is_invalid_only(tmp_path):
    user = tmp_path / "custom"
    user.mkdir()
    (user / "ok.yaml").write_text(USER_OK, encoding="utf-8")
    (user / "nosha.yaml").write_text(
        USER_OK.replace("my-gguf", "no-sha").replace(', sha256: "' + "c" * 64 + '"', ""), encoding="utf-8"
    )
    reg = load_registry(user_dirs=[user])
    assert reg.models["my-gguf"].status == "valid" and reg.models["my-gguf"].source == "user"
    bad = reg.models["no-sha"]
    assert bad.status == "invalid" and any("sha256" in e.message for e in bad.errors)
    assert reg.models["qwen3-vl-8b-instruct"].status == "valid"


@pytest.mark.unit
@pytest.mark.tc("INF-REG-004")
def test_inf_reg_004_broken_user_yaml_reports_file_and_line(tmp_path):
    user = tmp_path / "custom"
    user.mkdir()
    (user / "syntax.yaml").write_text("id: x\nkind: vlm\nvariants: [\n  - id: a\n", encoding="utf-8")
    (user / "field.yaml").write_text(
        USER_OK.replace("my-gguf", "bad-field").replace("kind: vlm", "kind: spaceship"), encoding="utf-8"
    )
    reg = load_registry(user_dirs=[user])
    errs = {Path(e.file).name: e for e in reg.errors}
    assert errs["syntax.yaml"].line >= 3
    assert errs["field.yaml"].line == 6 and "kind" in errs["field.yaml"].message
    assert "qwen3-vl-8b-instruct" in reg.models  # the rest of the registry still loads
    assert "bad-field" not in reg.models or reg.models["bad-field"].status == "invalid"


@pytest.mark.unit
@pytest.mark.tc("INF-REG-005")
def test_inf_reg_005_task_defaults_and_overrides():
    reg = load_registry()
    d = resolve_task_defaults(reg)
    assert d["caption"].model == "qwen3-vl-8b-instruct"
    assert d["classify"].model == "qwen3-vl-8b-instruct"
    assert d["embed"].model == "siglip2-so400m-384"
    d2 = resolve_task_defaults(
        reg, overrides={"caption": "qwen3-vl-4b-instruct@q4_k_m", "classify": "qwen3-vl-2b-instruct"}
    )
    assert (d2["caption"].model, d2["caption"].variant) == ("qwen3-vl-4b-instruct", "q4_k_m")
    assert d2["classify"].model == "qwen3-vl-2b-instruct"
    assert d2["embed"].model == "siglip2-so400m-384"
    with pytest.raises(KeyError):
        resolve_task_defaults(reg, overrides={"caption": "siglip2-so400m-384"})  # no caption capability


@pytest.mark.contract
@pytest.mark.tc("INF-REG-006")
def test_inf_reg_006_default_combos_are_redistributable():
    from opagent_registry.recommend import default_combo_variants

    reg = load_registry()
    keys = default_combo_variants(reg)
    assert keys  # every tier's default set
    for key in keys:
        model = reg.models[key.split("@")[0]]
        assert model.spec.license.weights in {"Apache-2.0", "MIT", "BSD-3-Clause"}, key
        assert model.spec.license.redistributable, key


@pytest.mark.release_gate
@pytest.mark.tc("INF-REG-013")
def test_inf_reg_013_release_requires_all_pinned():
    reg = load_registry()
    unpinned = [f"{m.spec.id}@{v}" for m in reg.models.values() for v, s in m.pin_state.items() if s != "pinned"]
    assert unpinned == [], f"run registry-pin.yml and merge the pin PR first: {unpinned}"
    for r in reg.runtimes.values():
        if not r.builtin:
            for p in r.platforms:
                assert p.sha256 and HEX64.match(p.sha256), f"{r.id} {p.os}-{p.arch} has no sha256"
