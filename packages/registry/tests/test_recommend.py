import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from opagent_core.hw import Gpu, HwSummary
from opagent_doctor.probes import load_profile
from opagent_registry.compat import compat
from opagent_registry.loader import load_registry
from opagent_registry.recommend import recommend

REG = load_registry()
ALL_PINNED = {k: "pinned" for k in REG.variant_keys()}


def rec(profile: str, pins=None):
    return recommend(REG, load_profile(profile), pin_states=ALL_PINNED if pins is None else pins)


@pytest.mark.unit
@pytest.mark.tc("INF-RT-001")
def test_inf_rt_001_5070ti_win():
    r = rec("rtx5070ti-win11")
    assert r.packs == ["llamacpp-cuda13"]
    assert r.vlm == "qwen3-vl-8b-instruct@q5_k_m"
    assert r.ort_ep == "directml"
    assert r.embed == "siglip2-so400m-384@onnx-fp16"
    assert r.download_bytes > 0 and r.download_minutes > 0
    assert not r.unsupported


@pytest.mark.unit
@pytest.mark.tc("INF-RT-002")
def test_inf_rt_002_4060_laptop():
    r = rec("rtx4060-laptop")
    assert r.vlm == "qwen3-vl-4b-instruct@q4_k_m"
    seq = [a for a in r.alternatives if a.vlm == "qwen3-vl-8b-instruct@q4_k_m"]
    assert seq and seq[0].mode == "sequential"


@pytest.mark.unit
@pytest.mark.tc("INF-RT-003")
@pytest.mark.parametrize("profile", ["m2-air-16", "m3-16"])
def test_inf_rt_003_mac16(profile):
    r = rec(profile)
    assert r.packs == []  # Metal is built in
    assert r.vlm == "qwen3-vl-4b-instruct@q4_k_m"
    assert r.ort_ep == "coreml"


@pytest.mark.unit
@pytest.mark.tc("INF-RT-004")
def test_inf_rt_004_mac8():
    r = rec("m1-8")
    assert r.vlm == "qwen3-vl-2b-instruct@q4_k_m"
    assert r.suggest_fast_mode


@pytest.mark.unit
@pytest.mark.tc("INF-RT-005")
def test_inf_rt_005_no_gpu():
    r = rec("no-gpu-linux")
    assert r.vlm is None and r.fast_mode
    assert "GPU_NOT_FOUND" in [w.code for w in r.warnings]
    opt = [a for a in r.alternatives if a.vlm]
    assert opt and "PERF_CPU_ONLY_SLOW" in [w.code for w in opt[0].warnings]
    assert r.embed == "siglip2-so400m-384@onnx-fp16-cpu" and r.ort_ep == "cpu"


@pytest.mark.unit
@pytest.mark.tc("INF-RT-006")
@pytest.mark.parametrize("profile", ["amd-rx7800-win", "intel-arc-linux"])
def test_inf_rt_006_amd_intel_vulkan(profile):
    r = rec(profile)
    assert r.packs == ["llamacpp-vulkan"]
    assert r.vlm == "qwen3-vl-8b-instruct@q5_k_m"


@pytest.mark.unit
@pytest.mark.tc("INF-RT-007")
def test_inf_rt_007_old_driver():
    r = rec("nvidia-drv535")
    w = [w for w in r.warnings if w.code == "GPU_DRIVER_TOO_OLD"]
    assert w, r.warnings
    assert r.packs == ["llamacpp-vulkan"]  # Vulkan fallback
    assert any(f["type"] == "open_url" for f in w[0].fixes)  # update driver
    assert r.vlm == "qwen3-vl-8b-instruct@q4_k_m"  # 10 GB class


@pytest.mark.unit
@pytest.mark.tc("INF-RT-007")
def test_inf_rt_007_driver_ok_for_cuda12_only():
    hw = load_profile("nvidia-drv535").with_gpu_driver("560.94")
    r = recommend(REG, hw, pin_states=ALL_PINNED)
    assert r.packs == ["llamacpp-cuda12"]


@pytest.mark.unit
@pytest.mark.tc("INF-RT-008")
def test_inf_rt_008_blackwell_with_cuda12_pack():
    r = rec("blackwell-cuda12pack")
    w = [w for w in r.warnings if w.code == "GPU_ARCH_UNSUPPORTED"]
    assert w
    assert w[0].fixes[0] == {"type": "download", "pack": "llamacpp-cuda13"}
    assert r.packs == ["llamacpp-cuda13"]


@pytest.mark.unit
@pytest.mark.tc("INF-RT-009")
def test_inf_rt_009_linux_nvidia():
    r = rec("linux-rtx3090")
    assert r.packs[0] in ("llamacpp-cuda13", "llamacpp-cuda12")
    assert "ort-cuda" in r.suggested_packs
    assert r.ort_ep == "cpu"


@pytest.mark.unit
@pytest.mark.tc("INF-RT-010")
@pytest.mark.parametrize("profile", ["intel-mac", "linux-arm64"])
def test_inf_rt_010_unsupported(profile):
    r = rec(profile)
    assert r.unsupported
    assert r.unsupported_reason == "RUNTIME_UNSUPPORTED_PLATFORM"
    assert r.docs_url.startswith("https://")
    assert r.vlm is None and r.packs == []


gpus = st.one_of(
    st.none(),
    st.builds(
        Gpu,
        vendor=st.sampled_from(["nvidia", "amd", "intel"]),
        name=st.sampled_from(["NVIDIA GeForce RTX 5070 Ti", "NVIDIA GeForce RTX 3080", "AMD Radeon", "Intel Arc"]),
        vram_total_gb=st.floats(1, 48),
        vram_used_external_gb=st.floats(0, 4),
        compute_capability=st.sampled_from([None, "6.1", "7.5", "8.6", "8.9", "12.0"]),
        driver=st.sampled_from([None, "470.10", "535.98", "560.94", "580.88"]),
    ),
)


@pytest.mark.property
@pytest.mark.tc("INF-RT-011")
@settings(max_examples=150, deadline=None)
@given(
    os=st.sampled_from(["windows", "linux"]),
    ram=st.floats(2, 128),
    gpu=gpus,
    packs=st.lists(st.sampled_from(["llamacpp-cuda12", "llamacpp-cuda13", "llamacpp-vulkan"]), unique=True),
)
def test_inf_rt_011_always_one_combo_and_cpu_path(os, ram, gpu, packs):
    hw = HwSummary(
        os=os,
        arch="x86_64",
        ram_gb=ram,
        cpu_count=8,
        gpus=(gpu,) if gpu else (),
        installed_packs=tuple((p, "x", None) for p in packs),
    )
    r = recommend(REG, hw, pin_states=ALL_PINNED)
    assert not r.unsupported
    assert r.embed is not None  # exactly one combination, never empty
    assert r.cpu_path in ("llamacpp-cpu", "siglip-fast")


@pytest.mark.property
@pytest.mark.tc("INF-RT-011")
@settings(max_examples=40, deadline=None)
@given(ram=st.sampled_from([8, 16, 24, 36, 48, 64]))
def test_inf_rt_011_mac(ram):
    hw = HwSummary(
        os="macos",
        arch="arm64",
        ram_gb=ram,
        cpu_count=8,
        gpus=(Gpu("apple", "Apple M2", ram, unified=True, budget_gb=ram * 0.67),),
    )
    r = recommend(REG, hw, pin_states=ALL_PINNED)
    assert r.embed is not None and r.cpu_path in ("llamacpp-cpu", "siglip-fast") and r.packs == []


@pytest.mark.unit
@pytest.mark.tc("INF-RT-012")
def test_inf_rt_012_unpinned_variant_skipped():
    pins = dict(ALL_PINNED)
    pins["qwen3-vl-8b-instruct@q5_k_m"] = "unpinned"
    r = rec("rtx5070ti-win11", pins)
    assert r.vlm == "qwen3-vl-8b-instruct@q4_k_m"
    assert "qwen3-vl-8b-instruct@q5_k_m" in r.not_pinned


@pytest.mark.unit
@pytest.mark.tc("INF-VRAM-006")
def test_inf_vram_006_mac_unified_budget():
    hw = load_profile("m3-16")
    g = hw.gpus[0]
    assert g.unified and g.budget_gb == pytest.approx(10.7, abs=0.01)
    m8, m4 = REG.models["qwen3-vl-8b-instruct"], REG.models["qwen3-vl-4b-instruct"]
    c8 = compat(m8, "q5_k_m", hw, REG.runtimes)
    c4 = compat(m4, "q4_k_m", hw, REG.runtimes)
    assert c8.vram["budget_gb"] == pytest.approx(6.7, abs=0.01)
    assert c8.level == "blocked" and "VRAM_INSUFFICIENT" in [r["code"] for r in c8.reasons]
    assert c4.level == "ok"


@pytest.mark.unit
@pytest.mark.tc("INF-VRAM-007")
def test_inf_vram_007_dxgi_and_vulkan_providers():
    amd = load_profile("amd-rx7800-win").gpus[0]
    assert amd.vendor == "amd" and amd.vram_total_gb == pytest.approx(16.0) and amd.budget_gb == pytest.approx(15.2)
    assert amd.vram_used_external_gb == pytest.approx(0.6) and amd.source == "dxgi"
    arc = load_profile("intel-arc-linux").gpus[0]
    assert arc.vendor == "intel" and arc.vram_total_gb == pytest.approx(16.0) and arc.budget_gb == pytest.approx(15.0)
    assert arc.source == "vulkan"
    # no budget data → compat uses the total and marks it estimated
    bare = HwSummary(os="linux", arch="x86_64", ram_gb=32, cpu_count=8, gpus=(Gpu("amd", "AMD Radeon", 16.0),))
    c = compat(REG.models["qwen3-vl-8b-instruct"], "q4_k_m", bare, REG.runtimes)
    assert c.vram["source"] == "estimated"


@pytest.mark.unit
@pytest.mark.tc("INF-VRAM-008")
def test_inf_vram_008_external_use_does_not_change_level():
    m = REG.models["qwen3-vl-8b-instruct"]
    idle = compat(m, "q5_k_m", load_profile("rtx5070ti-win11"), REG.runtimes)
    busy = compat(m, "q5_k_m", load_profile("game-running-5gb"), REG.runtimes)
    assert idle.level == busy.level == "ok"
    assert idle.fits_now is True
    assert busy.fits_now is False and busy.fits_now_reason == "GPU_BUSY_EXTERNAL"


@pytest.mark.unit
@pytest.mark.tc("INF-REG-014")
def test_inf_reg_014_empty_catalog_recommends_without_crashing(tmp_path):
    """回歸: the Docker image shipped without `registry/`; recommend() then indexed a model it never found and
    job.start answered 500. An empty catalog is a state to report (MODEL_NOT_PINNED), not a crash."""
    (tmp_path / "models").mkdir()
    (tmp_path / "runtimes").mkdir()
    (tmp_path / "pins.lock").write_text('{"schema_version": 1, "pins": []}', encoding="utf-8")
    (tmp_path / "gpu_classes.yaml").write_text("classes: []\n", encoding="utf-8")
    reg = load_registry(tmp_path)
    r = recommend(reg, load_profile("rtx5070ti-win11"))
    assert r.embed in r.not_pinned or r.embed is not None
    assert r.download_bytes >= 0
