"""RT-PACK-012 (09b §7.1): the v0.4 advanced packs exist in the registry and are offered only where they can run."""

import pytest
from opagent_doctor.probes import load_profile
from opagent_registry.compat import pack_failure
from opagent_registry.loader import BUILTIN_DIR, load_registry
from opagent_registry.recommend import advanced_packs

NEW = ("mlx", "vllm-cu128", "torch-cu128", "torch-mps", "torch-cpu")


@pytest.fixture(scope="module")
def reg():
    r = load_registry(BUILTIN_DIR)
    assert not r.errors, r.errors
    return r


@pytest.mark.unit
@pytest.mark.tc("RT-PACK-012")
def test_rt_pack_012_new_packs_are_registered_as_advanced(reg):
    for pid in NEW:
        p = reg.runtimes[pid]
        assert p.advanced and not p.builtin and p.license.get("spdx"), pid
    assert reg.runtimes["mlx"].provides == {"runtime": "mlx", "accel": "metal"}
    assert reg.runtimes["vllm-cu128"].provides == {"runtime": "vllm", "accel": "cuda"}
    assert reg.runtimes["torch-cu128"].provides["runtime"] == "torch"


@pytest.mark.unit
@pytest.mark.tc("RT-PACK-012")
@pytest.mark.parametrize(
    ("profile", "offered"),
    [
        ("m3-16", {"mlx", "torch-mps", "torch-cpu"}),
        ("linux-rtx3090", {"vllm-cu128", "torch-cu128", "torch-cpu"}),
        ("rtx5070ti-win11", {"torch-cu128", "torch-cpu"}),
    ],
)
def test_rt_pack_012_offered_only_where_they_run(reg, profile, offered):
    hw = load_profile(profile)
    got = {a["pack"] for a in advanced_packs(reg, hw) if a["pack"] in NEW}
    assert got == offered


@pytest.mark.unit
@pytest.mark.tc("RT-PACK-012")
def test_rt_pack_012_vllm_on_windows_explains_docker(reg):
    hw = load_profile("rtx5070ti-win11")
    assert pack_failure(reg.runtimes["vllm-cu128"], hw, reg.gpu_classes) == "RUNTIME_UNSUPPORTED_PLATFORM"
    assert pack_failure(reg.runtimes["mlx"], hw, reg.gpu_classes) == "RUNTIME_UNSUPPORTED_PLATFORM"
    blocked = {a["pack"]: a for a in advanced_packs(reg, hw, include_blocked=True)}
    v = blocked["vllm-cu128"]
    assert v["reason"] == "RUNTIME_UNSUPPORTED_PLATFORM"
    assert {f["type"] for f in v["fixes"]} == {"open_docs"} and "docker" in v["fixes"][0]["url"]
