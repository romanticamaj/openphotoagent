"""Environment Doctor checks (06 §3.2–3.4).

Each check is a pure function of a :class:`Ctx`: raw probe outputs (``probes``, the same format as
``fixtures/hw``) plus raw environment facts (``env``: imports, binaries, disk numbers, root states, ports, …). The
Worker fills ``env`` from the live machine (:func:`collect_env`); tests use fixtures. Every check declares an ``ok``
and a ``fail`` example so the registry can be verified as a whole (DOC-001). Checks never import torch (DOC-007).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .codes import fixes_for
from .probes import summarize

GB = 1024**3
ACTIONS_ALL = ["job.start", "scan", "model.download", "model.load", "output.apply", "output.undo", "runtime.install"]
FEATURE_ACTIONS: dict[str, list[str]] = {
    "caption.vlm.llamacpp": ["job.start", "model.load"],
    "caption.vlm.cuda": ["job.start", "model.load"],
    "caption.vlm.vllm": ["job.start", "model.load"],
    "caption.vlm.ollama": ["job.start", "model.load"],
    "caption.vlm.external": ["job.start"],
    "embed.onnx": ["job.start", "model.load"],
    "embed.gpu": ["job.start", "model.load"],
    "gpu": ["job.start", "model.load"],
    "scan": ["scan"],
    "decode.heic": ["scan"],
    "decode.raw": ["scan"],
    "video.thumbs": ["scan"],
    "download": ["model.download", "runtime.install"],
    "apply_output": ["output.apply", "output.undo"],
    "output.sidecar_xmp": ["output.apply"],
    "search.semantic": ["search"],
    "mcp": ["mcp.connect"],
    "api": ["app.start"],
    "notifications": [],
    "all": ACTIONS_ALL,
    "long_ops": [],
}
FEATURES: dict[str, tuple[list[str], list[str]]] = {  # feature -> (required checks, degrading checks) — 06 §3.4
    "caption.vlm.cuda": (["runtime.llamacpp", "gpu.driver", "gpu.cuda_runtime"], ["gpu.vram"]),
    "caption.vlm.llamacpp": (["runtime.llamacpp"], ["gpu.driver"]),
    "caption.vlm.vllm": (["runtime.vllm", "gpu.driver", "gpu.cuda_runtime", "wsl.gpu"], ["gpu.vram"]),
    "embed.onnx": ([], ["runtime.ort_ep"]),
    "embed.gpu": (["gpu.blackwell_torch"], []),
    "decode.heic": (["decode.heic"], []),
    "decode.raw": (["decode.raw"], []),
    "video.thumbs": ([], ["tool.ffmpeg"]),
    "scan": (["worker.link", "fs.roots"], ["disk.thumbs", "decode.heic", "decode.raw"]),
    "download": (["disk.models"], ["net.hf", "net.hf_token", "time.skew"]),
    "apply_output": (["worker.link", "fs.roots"], ["fs.long_paths", "tool.exiftool"]),
    "search.semantic": (["db.health"], ["search.index", "worker.link"]),
    "mcp": (["net.ports", "mcp.server"], ["search.index"]),
}


@dataclass
class Ctx:
    probes: dict[str, Any]
    env: dict[str, Any] = field(default_factory=dict)
    clock: Any = None
    deployment: str = "desktop"

    @property
    def hw(self) -> Any:
        return summarize(self.probes)

    def e(self, key: str, default: Any = None) -> Any:
        return self.env.get(key, default)


@dataclass
class CheckResult:
    check_id: str
    status: str  # ok | warn | fail | skip | unknown
    severity: str
    code: str | None = None
    observed: dict[str, Any] = field(default_factory=dict)
    message_key: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    fixes: list[dict[str, Any]] = field(default_factory=list)
    affects: list[str] = field(default_factory=list)
    duration_ms: int = 0
    checked_at: str = ""
    expires_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "affects_actions": sorted({a for f in self.affects for a in FEATURE_ACTIONS.get(f, [])}),
        }


@dataclass
class Out:
    status: str
    code: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    observed: dict[str, Any] = field(default_factory=dict)
    fixes: list[dict[str, Any]] | None = None
    message: str | None = None


def ok(**observed: Any) -> Out:
    return Out("ok", observed=observed)


def skip(why: str = "not applicable") -> Out:
    return Out("skip", observed={"reason": why})


def fx(ftype: str, fid: str, **payload: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"id": fid, "fix_id": fid, "type": ftype, "kind": ftype, "label_key": f"fix.{fid}"}
    if ftype == "choose_alternative":
        d["consequence"] = {"key": f"fix.{fid}.consequence"}
    if payload:
        d["payload"] = payload
    return d


@dataclass
class Check:
    id: str
    severity: str
    affects: list[str]
    fn: Callable[[Ctx], Any]
    examples: dict[str, tuple[str, dict[str, Any]]]
    scope: str = "host"
    group: str = "quick"
    ttl_s: int = 600

    def example(self, which: str) -> Ctx:
        profile, env = self.examples[which]
        from .probes import read_profile

        try:
            from opagent_testing.clock import FakeClock

            clk: Any = FakeClock()
        except ImportError:  # pragma: no cover
            from opagent_core.clock import SystemClock

            clk = SystemClock()
        return Ctx(probes=read_profile(profile)["probes"], env=dict(env), clock=clk)


CHECKS: dict[str, Check] = {}


def check(
    cid: str,
    severity: str,
    affects: list[str],
    ok_ex: tuple[str, dict[str, Any]],
    fail_ex: tuple[str, dict[str, Any]],
    scope: str = "host",
    group: str = "quick",
) -> Callable[[Callable[[Ctx], Any]], Callable[[Ctx], Any]]:
    def deco(fn: Callable[[Ctx], Any]) -> Callable[[Ctx], Any]:
        CHECKS[cid] = Check(cid, severity, affects, fn, {"ok": ok_ex, "fail": fail_ex}, scope, group)
        return fn

    return deco


NV, NOGPU, MAC, AMD = "rtx5070ti-win11", "no-gpu-linux", "m3-16", "amd-rx7800-win"


# ---- OS / GPU -------------------------------------------------------------------------------------------------------
@check("os.platform", "info", ["all"], (NV, {}), ("linux-arm64", {}))
def _os_platform(c: Ctx) -> Out:
    hw = c.hw
    if (hw.os, hw.arch) in {("windows", "x86_64"), ("macos", "arm64"), ("linux", "x86_64")}:
        return ok(os=hw.os, arch=hw.arch, container=hw.in_container)
    return Out("warn", "RUNTIME_UNSUPPORTED_PLATFORM", {"runtime": "OpenPhotoAgent", "os": f"{hw.os}-{hw.arch}"})


@check("os.arch_support", "warning", ["gpu"], (NV, {}), ("intel-mac", {}))
def _arch(c: Ctx) -> Out:
    hw = c.hw
    if (hw.os, hw.arch) in {("macos", "x86_64"), ("windows", "arm64")} or hw.arch not in ("x86_64", "arm64"):
        return Out("warn", None, {"os": hw.os, "arch": hw.arch}, fixes=[fx("open_docs", "platform_support")])
    return ok(arch=hw.arch)


def _tier(c: Ctx) -> tuple[str, list[str], str]:
    from opagent_registry.loader import load_registry
    from opagent_registry.recommend import recommend

    hw = c.hw
    r = recommend(load_registry(), hw, pin_states={})
    g = hw.primary_gpu
    if g is None or (hw.os != "macos" and g.vendor == "apple"):
        return "cpu", r.packs, "cpu"
    if g.vendor == "apple":
        return f"mac_{round(hw.ram_gb)}g", r.packs, "gpu"
    gb = g.vram_total_gb * 0.92
    t = "16g" if gb >= 14 else "12g" if gb >= 9 else "8g" if gb >= 5 else "small"
    return f"{g.vendor}_{t}", r.packs, "gpu"


@check("gpu.enumerate", "warning", ["gpu"], (NV, {}), (NOGPU, {}))
def _gpu_enum(c: Ctx) -> Out:
    tier, packs, combo = _tier(c)
    hw = c.hw
    obs = {"gpus": [g.name for g in hw.gpus], "tier": tier, "recommended_packs": packs, "combo": combo}
    if not hw.gpus:
        return Out("warn", "GPU_NOT_FOUND", {}, obs)
    return Out("ok", observed=obs)


@check("gpu.vram", "warning", ["caption.vlm.cuda"], (NV, {}), ("game-running-5gb", {}))
def _gpu_vram(c: Ctx) -> Out:
    from opagent_registry.compat import gpu_budget

    g = c.hw.primary_gpu
    if g is None:
        return skip("no gpu")
    budget, src = gpu_budget(g, c.hw.os)
    obs = {"total_gb": g.vram_total_gb, "budget_gb": round(budget, 2), "unified": g.unified, "source": src}
    if g.vram_used_external_gb >= 2.0:
        return Out(
            "warn",
            "GPU_BUSY_EXTERNAL",
            {"gb": round(g.vram_used_external_gb, 1), "process": ", ".join(g.processes)},
            {**obs, "processes": list(g.processes)},
        )
    return Out("ok", observed=obs)


def _nvidia(c: Ctx) -> Any:
    g = c.hw.primary_gpu
    return g if g is not None and g.vendor == "nvidia" else None


@check("gpu.driver", "blocker", ["caption.vlm.cuda"], (NV, {}), ("nvidia-drv535", {}))
def _gpu_driver(c: Ctx) -> Out:
    from opagent_core.hw import version_tuple

    g = _nvidia(c)
    if g is None:
        return skip("no nvidia gpu")
    need = "580.00"
    if g.driver and version_tuple(g.driver) < version_tuple(need):
        return Out("fail", "GPU_DRIVER_TOO_OLD", {"v": g.driver, "min": need})
    return ok(driver=g.driver)


@check("gpu.cuda_runtime", "blocker", ["caption.vlm.cuda"], (NV, {}), ("blackwell-cuda12pack", {}))
def _cuda_rt(c: Ctx) -> Out:
    from opagent_registry.compat import pack_failure
    from opagent_registry.loader import load_registry

    g = _nvidia(c)
    hw = c.hw
    if g is None:
        return skip("no nvidia gpu")
    reg = load_registry()
    for pid in sorted(hw.pack_ids):
        if pid.startswith("llamacpp-cuda") and pid in reg.runtimes:
            code = pack_failure(reg.runtimes[pid], hw, reg.gpu_classes)
            if code:
                return Out("fail", code, {"gpu": g.name, "sm": g.compute_capability, "v": g.driver, "pack": pid})
    return ok(packs=sorted(hw.pack_ids))


@check("gpu.blackwell_torch", "blocker", ["embed.gpu"], (NV, {}), (NV, {"torch": {"cuda": "12.4", "archs": ["sm_90"]}}))
def _blackwell_torch(c: Ctx) -> Out:
    t = c.e("torch")  # only reported when the optional torch pack is installed (never imported here)
    g = _nvidia(c)
    if not t or g is None:
        return skip("torch pack not installed")
    sm = "sm_" + str(g.compute_capability or "").replace(".", "")
    if sm not in t.get("archs", []):
        return Out(
            "fail",
            "GPU_ARCH_UNSUPPORTED",
            {"gpu": g.name, "sm": sm},
            fixes=[fx("download", "torch_cu128", pack="torch-cu128"), fx("choose_alternative", "use_llamacpp")],
        )
    return ok(torch=t)


@check("gpu.rocm", "warning", ["caption.vlm.llamacpp"], (NV, {}), ("intel-arc-linux", {"rocm": {"kfd": "EACCES"}}))
def _rocm(c: Ctx) -> Out:
    r = c.e("rocm")
    if not r:
        return skip("rocm not used")
    if r.get("kfd") != "ok":
        return Out(
            "warn",
            None,
            {"kfd": r.get("kfd")},
            fixes=[
                fx("copy_command", "rocm_groups", commands={"linux": "sudo usermod -aG render,video $USER"}),
                fx("open_docs", "rocm"),
            ],
        )
    return ok()


@check("gpu.metal", "warning", ["caption.vlm.llamacpp"], (MAC, {}), (MAC, {"macos_version": "12.6"}))
def _metal(c: Ctx) -> Out:
    hw = c.hw
    if hw.os != "macos":
        return skip("not macOS")
    v = str(c.e("macos_version", "14.0"))
    if int(v.split(".")[0]) < 13:
        return Out("warn", None, {"v": v}, fixes=[fx("open_docs", "macos_version")])
    g = hw.primary_gpu
    return ok(device=g.name if g else None)


@check("gpu.docker_passthrough", "blocker", ["gpu"], (NV, {}), ("docker-no-passthrough", {"gpu_requested": True}))
def _docker(c: Ctx) -> Out:
    hw = c.hw
    if not hw.in_container:
        return skip("not in a container")
    if c.e("gpu_requested") and not hw.gpus:
        return Out("fail", "GPU_DOCKER_NO_PASSTHROUGH", {})
    return ok()


@check("wsl.gpu", "warning", ["caption.vlm.vllm"], (NV, {}), (NV, {"wsl": {"wanted": True, "libcuda": False}}))
def _wsl(c: Ctx) -> Out:
    w = c.e("wsl") or {}
    if c.hw.os != "windows" or not w.get("wanted"):
        return skip("vLLM not selected")
    if not w.get("libcuda"):
        return Out("warn", "WSL_GPU_UNAVAILABLE", {})
    return ok()


@check("sys.ram", "warning", ["caption.vlm.llamacpp"], (NV, {}), ("m1-8", {"ram_needed_gb": 16}))
def _ram(c: Ctx) -> Out:
    need = float(c.e("ram_needed_gb", 0))
    have = c.hw.ram_gb
    if need and have and have < need:
        return Out("warn", "RAM_INSUFFICIENT", {"need": need, "total": round(have)})
    return ok(ram_gb=round(have, 1))


@check("sys.power", "info", ["long_ops"], (NV, {}), ("rtx4060-laptop", {}))
def _power(c: Ctx) -> Out:
    if c.hw.on_battery or c.e("on_battery"):
        return Out("warn", None, {}, fixes=[fx("open_settings", "keep_awake")], message="on_battery")
    return ok()


# ---- disks ----------------------------------------------------------------------------------------------------------
@check(
    "disk.models",
    "blocker",
    ["download"],
    (NV, {"disk": {"models_free": 100 * GB}}),
    (NV, {"disk": {"models_free": GB, "next_download": 8 * GB}}),
)
def _disk_models(c: Ctx) -> Out:
    d = c.e("disk") or {}
    free = d.get("models_free")
    if free is None:
        return skip("unknown")
    need = int(d.get("next_download") or 0)
    if need and free < need * 1.05:
        return Out("fail", "DISK_INSUFFICIENT_MODELS", {"req": int(need * 1.05), "free": free})
    if free < 20 * GB:
        return Out(
            "warn",
            None,
            {"free": free},
            fixes=[fx("open_settings", "models_storage"), fx("auto_fix", "cleanup_downloads")],
        )
    return ok(free=free)


@check(
    "disk.thumbs",
    "blocker",
    ["scan"],
    (NV, {"disk": {"thumbs_free": 100 * GB, "photos": 1000}}),
    (NV, {"disk": {"thumbs_free": 1000, "photos": 1000}}),
)
def _disk_thumbs(c: Ctx) -> Out:
    d = c.e("disk") or {}
    free = d.get("thumbs_free")
    if free is None:
        return skip("unknown")
    need = int(d.get("photos") or 0) * 60_000
    if need > free:
        return Out("fail", "DISK_INSUFFICIENT_THUMBS", {"req": need, "free": free})
    return ok(free=free, need=need)


@check(
    "disk.db",
    "blocker",
    ["all"],
    (NV, {"disk": {"db_free": 50 * GB}}),
    (NV, {"disk": {"db_free": GB // 2}}),
    scope="cp",
)
def _disk_db(c: Ctx) -> Out:
    free = (c.e("disk") or {}).get("db_free")
    if free is None:
        return skip("unknown")
    if free < GB:
        return Out("fail", None, {"free": free}, fixes=[fx("open_docs", "storage")])
    return ok(free=free)


# ---- runtimes -------------------------------------------------------------------------------------------------------
def _pack_test(c: Ctx, prefix: str) -> tuple[str, str] | None:
    for pid, st in sorted((c.e("packs") or {}).items()):
        if pid.startswith(prefix) and str(st.get("selftest", "ok")) != "ok":
            return pid, str(st.get("selftest"))
    return None


@check(
    "runtime.packs",
    "blocker",
    ["caption.vlm.llamacpp"],
    (NV, {"packs": {"llamacpp-cuda13": {"selftest": "ok"}}}),
    (NV, {"packs": {"llamacpp-cuda13": {"selftest": "error: dll"}}}),
)
def _packs(c: Ctx) -> Out:
    bad = _pack_test(c, "")
    if bad:
        return Out("fail", "RUNTIME_PACK_BROKEN", {"pack": bad[0], "reason": bad[1]})
    return ok(packs=sorted(c.e("packs") or {}))


@check("runtime.vllm", "blocker", ["caption.vlm.vllm"], (NV, {}), (NV, {"vllm": {"wanted": True}}))
def _vllm(c: Ctx) -> Out:
    v = c.e("vllm") or {}
    if not v.get("wanted"):
        return skip("vLLM not selected")
    if c.hw.os != "linux":
        return Out("fail", "RUNTIME_UNSUPPORTED_PLATFORM", {"runtime": "vLLM", "os": c.hw.os})
    return ok() if v.get("import_ok") else Out("fail", "RUNTIME_PACK_MISSING", {"pack": "vllm-cu128"})


@check(
    "runtime.llamacpp",
    "blocker",
    ["caption.vlm.llamacpp", "caption.vlm.cuda"],
    (NV, {"packs": {"llamacpp-cpu": {"selftest": "ok"}}}),
    (NV, {"packs": {"llamacpp-cpu": {"selftest": "error: exit 1"}}}),
)
def _llamacpp(c: Ctx) -> Out:
    packs = {k: v for k, v in (c.e("packs") or {}).items() if k.startswith("llamacpp-")}
    if not packs:
        return skip("llama.cpp not installed")
    bad = _pack_test(c, "llamacpp-")
    if bad:
        return Out("fail", "RUNTIME_PACK_BROKEN", {"pack": bad[0], "reason": bad[1]})
    return ok(packs=sorted(packs))


@check(
    "runtime.ort_ep",
    "warning",
    ["embed.onnx"],
    (NV, {"ort": {"expected": "DmlExecutionProvider", "available": ["DmlExecutionProvider", "CPUExecutionProvider"]}}),
    ("linux-rtx3090", {"ort": {"expected": "CUDAExecutionProvider", "available": ["CPUExecutionProvider"]}}),
)
def _ort(c: Ctx) -> Out:
    o = c.e("ort") or {}
    exp = o.get("expected")
    if not exp:
        return skip("no accelerated EP expected")
    if exp not in (o.get("available") or []):
        return Out("warn", "ORT_EP_UNAVAILABLE", {"ep": exp.replace("ExecutionProvider", "")})
    return ok(ep=exp)


@check(
    "runtime.ollama",
    "warning",
    ["caption.vlm.ollama"],
    (NV, {}),
    (NV, {"ollama": {"wanted": True, "reachable": False}}),
)
def _ollama(c: Ctx) -> Out:
    o = c.e("ollama") or {}
    if not o.get("wanted"):
        return skip("no ollama variant")
    if not o.get("reachable"):
        return Out("warn", "OLLAMA_NOT_RUNNING", {"url": o.get("url", "http://127.0.0.1:11434")})
    return ok()


# ---- decoders / tools -----------------------------------------------------------------------------------------------
def _imp(c: Ctx, mod: str) -> bool:
    return bool((c.e("imports") or {}).get(mod, True))


@check(
    "decode.heic",
    "warning",
    ["decode.heic"],
    (NV, {"imports": {"pillow_heif": True}}),
    (NV, {"imports": {"pillow_heif": False}}),
)
def _heic(c: Ctx) -> Out:
    if not _imp(c, "pillow_heif"):
        return Out("warn", "DECODE_HEIC_MISSING", {"n": int((c.e("counts") or {}).get("heic", 0))})
    return ok()


@check(
    "decode.raw",
    "warning",
    ["decode.raw"],
    (NV, {"imports": {"rawpy": True}}),
    (NV, {"imports": {"rawpy": False}, "counts": {"raw": 120}}),
)
def _raw(c: Ctx) -> Out:
    if not _imp(c, "rawpy") and int((c.e("counts") or {}).get("raw", 0)) > 0:
        return Out("warn", "DECODE_RAW_MISSING", {"n": int((c.e("counts") or {}).get("raw", 0))})
    return ok(rawpy=_imp(c, "rawpy"))


@check(
    "tool.ffmpeg",
    "info",
    ["video.thumbs"],
    (NV, {"which": {"ffmpeg": "/usr/bin/ffmpeg"}}),
    (NV, {"which": {"ffmpeg": None}, "counts": {"video": 3}}),
)
def _ffmpeg(c: Ctx) -> Out:
    if not (c.e("which") or {}).get("ffmpeg") and int((c.e("counts") or {}).get("video", 0)) > 0:
        return Out("warn", "FFMPEG_MISSING", {"n": int((c.e("counts") or {}).get("video", 0))})
    return ok()


@check(
    "tool.exiftool",
    "warning",
    ["output.sidecar_xmp"],
    (NV, {}),
    (NV, {"which": {"exiftool": None}, "exif_writes": True}),
)
def _exiftool(c: Ctx) -> Out:
    if c.e("exif_writes") and not (c.e("which") or {}).get("exiftool"):
        return Out("warn", None, {}, fixes=[fx("download", "exiftool", pack="exiftool")])
    return ok()


# ---- filesystem -----------------------------------------------------------------------------------------------------
@check(
    "fs.roots",
    "blocker",
    ["scan", "apply_output"],
    (NV, {"roots": [{"alias": "photos", "state": "ok", "mode": "ro"}]}),
    (MAC, {"roots": [{"alias": "photos", "state": "EPERM", "mode": "ro"}]}),
)
def _roots(c: Ctx) -> Out:
    for r in c.e("roots") or []:
        st = r.get("state", "ok")
        if st == "EPERM" or st == "EACCES":
            fixes = (
                [fx("auto_fix", "open_tcc_settings"), fx("open_settings", "allowed_roots")]
                if c.hw.os == "macos"
                else [
                    fx("open_settings", "allowed_roots"),
                    fx("copy_command", "chown_volume", commands={"linux": "sudo chown -R $USER <folder>"}),
                ]
            )
            return Out("fail", "FS_PERMISSION_DENIED", {"path": r["alias"]}, fixes=fixes)
        if st in ("ENOENT", "unavailable"):
            return Out("fail", "FS_ROOT_UNAVAILABLE", {"root": r["alias"]})
        if r.get("mode") == "rw" and r.get("writable") is False:
            return Out("fail", "FS_NOT_WRITABLE", {"path": r["alias"]})
    return ok(roots=[r.get("alias") for r in c.e("roots") or []])


@check("fs.long_paths", "info", ["apply_output"], (NV, {"long_paths_enabled": 1}), (NV, {"long_paths_enabled": 0}))
def _long_paths(c: Ctx) -> Out:
    if c.hw.os != "windows":
        return skip("not Windows")
    if int(c.e("long_paths_enabled", 1)) == 0:
        cmd = 'reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f'
        return Out(
            "warn",
            None,
            {},
            fixes=[fx("copy_command", "enable_long_paths", commands={"windows": cmd}), fx("open_docs", "long_paths")],
        )
    return ok()


# ---- network --------------------------------------------------------------------------------------------------------
@check("net.hf", "warning", ["download"], (NV, {"net": {"hf": "ok"}}), (NV, {"net": {"hf": "tls"}}), group="full")
def _net_hf(c: Ctx) -> Out:
    st = (c.e("net") or {}).get("hf")
    if st is None:
        return skip("not probed")
    if st == "tls":
        return Out("warn", "NET_TLS_INTERCEPTED", {})
    if st != "ok":
        return Out("warn", "NET_UNREACHABLE", {"host": "huggingface.co"})
    return ok()


@check("net.hf_token", "warning", ["download"], (NV, {}), (NV, {"net": {"hf_token": "invalid"}}), group="full")
def _net_token(c: Ctx) -> Out:
    st = (c.e("net") or {}).get("hf_token")
    if st in (None, "none"):
        return skip("no token")
    if st != "valid":
        return Out("warn", "HF_AUTH_REQUIRED", {})
    return ok()


@check(
    "net.ports",
    "blocker",
    ["api", "mcp"],
    (NV, {}),
    (NV, {"ports": {"52731": {"pid": 9, "process": "other.exe", "healthz_opagent": False}}}),
)
def _ports(c: Ctx) -> Out:
    for port, h in sorted((c.e("ports") or {}).items()):
        if not h:
            continue
        if h.get("healthz_opagent"):
            return Out(
                "fail",
                "NET_PORT_IN_USE",
                {"port": int(port), "process": "OpenPhotoAgent"},
                fixes=[fx("open_settings", "switch_to_running", port=int(port))],
            )
        fixes = fixes_for("NET_PORT_IN_USE", c.deployment, c.hw.os)
        if c.deployment != "desktop":
            fixes = [
                fx(
                    "copy_command",
                    "set_port",
                    commands={"linux": "OPAGENT_PORT=52732 opagent api", "windows": "set OPAGENT_PORT=52732"},
                )
            ]
        return Out("fail", "NET_PORT_IN_USE", {"port": int(port), "process": h.get("process")}, fixes=fixes)
    return ok()


# ---- control plane / client ----------------------------------------------------------------------------------------
@check(
    "worker.link",
    "blocker",
    ["scan", "apply_output"],
    (NV, {"worker": {"heartbeat_age_s": 3}}),
    (NV, {"worker": {"heartbeat_age_s": 300}}),
    scope="cp",
)
def _link(c: Ctx) -> Out:
    w = c.e("worker") or {}
    age = w.get("heartbeat_age_s")
    if age is None:
        return skip("evaluated by the control plane")
    if age > 90:
        return Out("fail", "WORKER_OFFLINE", {"name": w.get("name", "worker"), "duration": f"{int(age)} s"})
    return ok(rtt_ms=w.get("rtt_ms"))


@check(
    "worker.version",
    "blocker",
    ["all"],
    (NV, {"worker": {"protocol": "1.0"}}),
    (NV, {"worker": {"protocol": "2.0"}}),
    scope="cp",
)
def _wversion(c: Ctx) -> Out:
    v = (c.e("worker") or {}).get("protocol")
    if v is None:
        return skip("evaluated by the control plane")
    if str(v).split(".")[0] != "1":
        return Out("fail", "WORKER_VERSION_MISMATCH", {"w": v, "s": "1.0"})
    return ok()


@check("time.skew", "blocker", ["download"], (NV, {"time_skew_s": 3}), (NV, {"time_skew_s": 400}))
def _skew(c: Ctx) -> Out:
    s = abs(float(c.e("time_skew_s", 0)))
    cmds = {
        "windows": "w32tm /resync",
        "linux": "sudo timedatectl set-ntp true",
        "macos": "sudo sntp -sS time.apple.com",
    }
    fixes = fixes_for("TIME_SKEW", c.deployment, c.hw.os, time_sync={"commands": cmds})
    if s > 300:
        return Out("fail", "TIME_SKEW", {"s": int(s)}, fixes=fixes)
    if s > 60:
        return Out("warn", "TIME_SKEW", {"s": int(s)}, fixes=fixes)
    return ok(skew_s=s)


@check(
    "client.notifications",
    "info",
    ["notifications"],
    (NV, {"notifications": "granted"}),
    (NV, {"notifications": "denied"}),
    scope="client",
)
def _notif(c: Ctx) -> Out:
    st = c.e("notifications")
    if st is None:
        return skip("client side")
    if st != "granted":
        return Out("warn", None, {}, fixes=[fx("auto_fix", "request_permission")])
    return ok()


@check(
    "db.health",
    "blocker",
    ["search.semantic", "all"],
    (NV, {"db": {"ok": True, "pgvector": True}}),
    (NV, {"db": {"ok": True, "pgvector": False}}),
    scope="cp",
)
def _db(c: Ctx) -> Out:
    d = c.e("db")
    if d is None:
        return skip("evaluated by the control plane")
    if not d.get("ok") or not d.get("pgvector"):
        return Out(
            "fail",
            None,
            d,
            fixes=[
                fx(
                    "copy_command",
                    "db_upgrade",
                    commands={"linux": "opagent db upgrade", "windows": "opagent db upgrade"},
                ),
                fx("open_docs", "database"),
            ],
        )
    return ok()


@check(
    "search.index",
    "warning",
    ["search.semantic"],
    (NV, {"search": {"coverage": 1.0, "model_match": True}}),
    (NV, {"search": {"coverage": 0.87, "model_match": True}}),
    scope="cp",
)
def _search(c: Ctx) -> Out:
    s = c.e("search")
    if s is None:
        return skip("evaluated by the control plane")
    if not s.get("model_match", True):
        return Out("warn", "SEARCH_MODEL_MISMATCH", {"old": s.get("old"), "new": s.get("new")})
    if float(s.get("coverage", 1.0)) < 1.0:
        return Out("warn", "SEARCH_INDEX_INCOMPLETE", {"n": s.get("missing", 0)})
    return ok()


@check(
    "mcp.server",
    "warning",
    ["mcp"],
    (NV, {"mcp": {"enabled": True}}),
    (NV, {"mcp": {"enabled": False, "wanted": True}}),
    scope="cp",
)
def _mcp(c: Ctx) -> Out:
    m = c.e("mcp")
    if m is None:
        return skip("MCP arrives in v0.2")
    if m.get("wanted") and not m.get("enabled"):
        return Out("warn", "MCP_DISABLED", {})
    return ok()


# ---- runner ---------------------------------------------------------------------------------------------------------
SEV_STATUS = {"blocker": "fail", "warning": "warn", "info": "warn"}


def _finish(chk: Check, out: Out, ctx: Ctx, t0: float) -> CheckResult:
    if ctx.clock is None:
        from opagent_core.clock import SystemClock

        ctx.clock = SystemClock()
    now: datetime = ctx.clock.now()
    fixes = out.fixes
    if fixes is None:
        fixes = fixes_for(out.code, ctx.deployment, ctx.hw.os) if out.code else []
    for i, f in enumerate(fixes):
        f["primary"] = i == 0
    msg = f"checks.{chk.id}.{out.message or out.status}"
    return CheckResult(
        check_id=chk.id,
        status=out.status,
        severity=chk.severity,
        code=out.code,
        observed=out.observed,
        message_key=msg,
        params=out.params,
        fixes=fixes,
        affects=list(chk.affects),
        duration_ms=int((ctx.clock.monotonic() - t0) * 1000) if ctx.clock else 0,
        checked_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=chk.ttl_s)).isoformat(),
    )


async def _one(chk: Check, ctx: Ctx, fn: Callable[[Ctx], Any], timeout_s: float) -> CheckResult:
    if ctx.clock is None:
        from opagent_core.clock import SystemClock

        ctx.clock = SystemClock()
    t0 = ctx.clock.monotonic()
    try:
        res = fn(ctx)
        if inspect.isawaitable(res):
            task = asyncio.ensure_future(res)
            timer = asyncio.ensure_future(ctx.clock.sleep(timeout_s))
            done, _ = await asyncio.wait({task, timer}, return_when=asyncio.FIRST_COMPLETED)
            if task not in done:  # DOC-009: a hung probe never blocks the doctor
                task.cancel()
                return _finish(chk, Out("unknown", None, {}, fixes=[fx("retry", "rerun_check", check=chk.id)]), ctx, t0)
            timer.cancel()
            res = task.result()
        if res is None:
            res = ok()
        if res.status == "fail" and chk.severity != "blocker":  # a check never exceeds its declared severity
            res.status = "warn"
        return _finish(chk, res, ctx, t0)
    except Exception as e:
        out = Out("warn", "CHECK_CRASHED", {"check": chk.id}, {"error": type(e).__name__})
        return _finish(chk, out, ctx, t0)


async def run_checks(
    ctx: Ctx,
    only: list[str] | None = None,
    override: dict[str, Callable[[Ctx], Any]] | None = None,
    timeout_s: float = 2.0,
    group: str = "full",
) -> list[CheckResult]:
    ids = only or [cid for cid, c in CHECKS.items() if group == "full" or c.group == "quick"]
    return list(
        await asyncio.gather(*(_one(CHECKS[i], ctx, (override or {}).get(i, CHECKS[i].fn), timeout_s) for i in ids))
    )


def features(results: list[CheckResult]) -> dict[str, dict[str, Any]]:
    by = {r.check_id: r for r in results}
    out = {}
    for feat, (req, deg) in FEATURES.items():
        failing = [c for c in req if c in by and by[c].status == "fail"]
        degraded = [c for c in deg if c in by and by[c].status in ("fail", "warn")]
        out[feat] = {"available": not failing, "blocked_by": failing, "degraded_by": degraded}
    return out


def to_preflight(results: list[CheckResult]) -> list[dict[str, Any]]:
    """Doctor results as the worker reports them to the control plane (consumed by preflight's doctor gate)."""
    out = []
    for r in results:
        if r.status in ("fail", "warn", "crashed") or r.code == "CHECK_CRASHED":
            d = r.to_dict()
            out.append(
                {
                    "id": r.check_id,
                    "status": "crashed" if r.code == "CHECK_CRASHED" else r.status,
                    "code": r.code,
                    "params": r.params,
                    "affects": d["affects_actions"],
                    "fixes": r.fixes,
                }
            )
    return out
