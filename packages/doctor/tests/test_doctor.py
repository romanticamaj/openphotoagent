import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from opagent_doctor.checks import CHECKS, Ctx, features, run_checks
from opagent_doctor.probes import read_profile
from opagent_testing.clock import FakeClock

ROOT = Path(__file__).resolve().parents[3]
I18N = {
    lang: json.loads((ROOT / f"packages/i18n/checks.{lang}.json").read_text(encoding="utf-8"))
    for lang in ("zh-TW", "en")
}


def ctx(profile="rtx5070ti-win11", **env):
    return Ctx(probes=read_profile(profile)["probes"], env=env, clock=FakeClock())


def run(c, **kw):
    return {r.check_id: r for r in asyncio.run(run_checks(c, **kw))}


@pytest.mark.contract
@pytest.mark.tc("DOC-001")
def test_doc_001_registry_complete():
    assert len(CHECKS) == 37
    for cid, chk in CHECKS.items():
        for lang in ("zh-TW", "en"):
            assert I18N[lang].get(cid, {}).get("title"), (lang, cid)
        assert chk.affects and chk.severity in ("blocker", "warning", "info")
        ok_ctx, fail_ctx = chk.example("ok"), chk.example("fail")
        ok = asyncio.run(run_checks(ok_ctx, only=[cid]))[0]
        bad = asyncio.run(run_checks(fail_ctx, only=[cid]))[0]
        assert ok.status in ("ok", "skip"), (cid, ok)
        assert bad.status in ("warn", "fail"), (cid, bad)
        assert bad.fixes, cid  # ≥ 1 fix when not ok


@pytest.mark.unit
@pytest.mark.tc("DOC-002")
def test_doc_002_rtx5070ti():
    rs = run(ctx())
    assert all(rs[c].status == "ok" for c in ("gpu.enumerate", "gpu.vram", "gpu.driver"))
    rep = rs["gpu.enumerate"].observed
    assert rep["recommended_packs"] == ["llamacpp-cuda13"] and rep["tier"] == "nvidia_16g"


@pytest.mark.unit
@pytest.mark.tc("DOC-003")
def test_doc_003_mac_m3():
    rs = run(ctx("m3-16"))
    assert rs["gpu.metal"].status == "ok"
    assert rs["gpu.vram"].observed["unified"] is True and rs["gpu.vram"].observed["budget_gb"] == pytest.approx(
        6.7, abs=0.01
    )
    assert rs["gpu.enumerate"].observed["recommended_packs"] == []


@pytest.mark.unit
@pytest.mark.tc("DOC-004")
def test_doc_004_no_gpu():
    rs = run(ctx("no-gpu-linux"))
    r = rs["gpu.enumerate"]
    assert r.status == "warn" and r.code == "GPU_NOT_FOUND"
    assert rs["gpu.enumerate"].observed["combo"] == "cpu"


@pytest.mark.unit
@pytest.mark.tc("DOC-005")
def test_doc_005_old_driver_only_blocks_cuda():
    rs = asyncio.run(run_checks(ctx("nvidia-drv535")))
    by = {r.check_id: r for r in rs}
    assert by["gpu.driver"].status == "fail" and by["gpu.driver"].code == "GPU_DRIVER_TOO_OLD"
    f = features(rs)
    assert f["caption.vlm.cuda"]["available"] is False
    assert f["caption.vlm.llamacpp"]["available"] is True  # Vulkan / CPU build


@pytest.mark.unit
@pytest.mark.tc("DOC-006")
@pytest.mark.parametrize("profile", ["amd-rx7800-win", "intel-arc-linux"])
def test_doc_006_amd_intel_vulkan(profile):
    rs = run(ctx(profile))
    assert rs["gpu.enumerate"].observed["recommended_packs"] == ["llamacpp-vulkan"]
    assert all(r.status != "crashed" for r in rs.values())


@pytest.mark.unit
@pytest.mark.tc("DOC-007")
def test_doc_007_no_torch_needed():
    code = (
        "import asyncio, sys\n"
        "from opagent_testing.clock import FakeClock\n"
        "from opagent_doctor.checks import Ctx, run_checks\n"
        "from opagent_doctor.probes import read_profile\n"
        "rs = asyncio.run(run_checks(Ctx(probes=read_profile('rtx5070ti-win11')['probes'], env={}, clock=FakeClock())))\n"
        "assert len(rs) == 37\n"
        "assert 'torch' not in sys.modules\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr


@pytest.mark.unit
@pytest.mark.tc("DOC-008")
def test_doc_008_crashing_check_isolated():
    def boom(c):
        raise RuntimeError("nvml exploded")

    rs = run(ctx(), override={"gpu.vram": boom})
    assert rs["gpu.vram"].status == "warn" and rs["gpu.vram"].code == "CHECK_CRASHED"
    assert rs["gpu.vram"].params["check"] == "gpu.vram"
    assert rs["gpu.driver"].status == "ok"


@pytest.mark.unit
@pytest.mark.tc("DOC-009")
def test_doc_009_check_timeout():
    c = ctx()

    async def slow(cx):
        await cx.clock.sleep(5)

    async def go():
        t = asyncio.create_task(run_checks(c, override={"net.hf": slow}, only=["net.hf"], timeout_s=2))
        await c.clock.tick(0)
        await c.clock.tick(2)
        return await t

    [r] = asyncio.run(go())
    assert r.status == "unknown" and [f["type"] for f in r.fixes] == ["retry"]


@pytest.mark.unit
@pytest.mark.tc("DOC-011")
def test_doc_011_disk_models_thresholds():
    GB = 1024**3
    r = run(ctx(disk={"models_free": 5 * GB, "next_download": 8 * GB}), only=["disk.models"])["disk.models"]
    assert r.status == "fail" and r.code == "DISK_INSUFFICIENT_MODELS"
    r = run(ctx(disk={"models_free": 15 * GB, "next_download": 8 * GB}), only=["disk.models"])["disk.models"]
    assert r.status == "warn"
    r = run(ctx(disk={"models_free": 50 * GB, "next_download": 8 * GB}), only=["disk.models"])["disk.models"]
    assert r.status == "ok"


@pytest.mark.unit
@pytest.mark.tc("DOC-012")
def test_doc_012_macos_tcc():
    r = run(ctx("m3-16", roots=[{"alias": "photos", "state": "EPERM", "mode": "ro"}]), only=["fs.roots"])["fs.roots"]
    assert r.status == "fail" and r.code == "FS_PERMISSION_DENIED"
    assert r.fixes[0]["type"] == "auto_fix" and r.fixes[0]["id"] == "open_tcc_settings"


@pytest.mark.unit
@pytest.mark.tc("DOC-013")
def test_doc_013_windows_long_paths():
    r = run(ctx(long_paths_enabled=0), only=["fs.long_paths"])["fs.long_paths"]
    assert r.status == "warn" and r.severity == "info"
    cmd = next(f for f in r.fixes if f["type"] == "copy_command")
    assert "LongPathsEnabled" in cmd["payload"]["commands"]["windows"]


@pytest.mark.unit
@pytest.mark.tc("DOC-014")
def test_doc_014_time_skew():
    w = run(ctx(time_skew_s=90), only=["time.skew"])["time.skew"]
    b = run(ctx(time_skew_s=360), only=["time.skew"])["time.skew"]
    assert w.status == "warn" and b.status == "fail" and b.code == "TIME_SKEW"
    cmd = next(f for f in b.fixes if f["type"] == "copy_command")["payload"]["commands"]
    assert "w32tm /resync" in cmd["windows"] and "timedatectl set-ntp true" in cmd["linux"] and "sntp" in cmd["macos"]


@pytest.mark.unit
@pytest.mark.tc("DOC-015")
def test_doc_015_battery_info():
    r = run(ctx("rtx4060-laptop"), only=["sys.power"])["sys.power"]
    assert r.status == "warn" and r.severity == "info" and r.message_key == "checks.sys.power.on_battery"


@pytest.mark.unit
@pytest.mark.tc("DOC-018")
def test_doc_018_feature_mapping():
    rs = asyncio.run(run_checks(ctx(packs={"llamacpp-cpu": {"selftest": "error: exit 1"}})))
    f = features(rs)
    assert f["caption.vlm.llamacpp"]["available"] is False
    assert f["embed.onnx"]["available"] is True


@pytest.mark.unit
@pytest.mark.os_windows
@pytest.mark.tc("DOC-021")
def test_doc_021_windows_reports_its_memory():
    """回歸: RAM came only from /proc/meminfo, so Windows reported 0 GB to the recommender and the doctor."""
    from opagent_doctor.probes import collect_probes, summarize

    assert summarize(collect_probes()).ram_gb > 1
