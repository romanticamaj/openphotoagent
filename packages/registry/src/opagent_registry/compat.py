"""Compatibility of a (variant, worker) pair: ok / degraded / blocked + reasons + prep (06 §1.2).

Hard checks stop at the first failure (platform → accelerator → driver → memory); soft issues and preparation
steps are always collected. ``prep`` never changes the level (MDL-003).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from opagent_core.hw import Gpu, HwSummary, version_tuple
from opagent_core.vram import budget_gb, can_coload

from .loader import LoadedModel
from .schema import GpuClass, RuntimePack, Variant

SAFETY = 0.92
DESKTOP_RESERVE_GB = {"windows": 0.8}
UNIFIED_RESERVE_GB = 4.0  # OS + our own processes on Apple Silicon (08 §3.3)
RESIDENT_SMALL_GB = 1.5  # SigLIP2 in the small slot (03 §4.2)
ACCEL_ORDER = ("cuda", "rocm", "vulkan", "metal", "directml", "coreml", "cpu")
LARGE_KINDS = {"vlm", "captioner"}
CPU_ITEMS_PER_S = {"vlm": 0.05, "captioner": 0.5}  # p10 CPU priors (03 §4.6 【估算】) for PERF_CPU_ONLY_SLOW


def _issue(code: str, **params: Any) -> dict[str, Any]:
    return {"code": code, "params": params}


@dataclass
class Compat:
    variant: str
    level: str  # ok | degraded | blocked
    reasons: list[dict[str, Any]] = field(default_factory=list)
    prep: list[dict[str, Any]] = field(default_factory=list)
    vram: dict[str, Any] = field(default_factory=dict)
    fits_now: bool | None = None
    fits_now_reason: str | None = None
    pack: str | None = None
    accel: str | None = None

    @property
    def label_key(self) -> str:
        return f"compat.{self.level}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "level": self.level,
            "label_key": self.label_key,
            "reasons": self.reasons,
            "prep": self.prep,
            "vram": self.vram,
            "fits_now": self.fits_now,
            "fits_now_reason": self.fits_now_reason,
            "pack": self.pack,
            "accel": self.accel,
        }


def gpu_cc(gpu: Gpu, classes: Iterable[GpuClass] = ()) -> str | None:
    if gpu.compute_capability:
        return gpu.compute_capability
    for c in classes:
        if any(m.lower() in gpu.name.lower() for m in c.match):
            return c.compute_capability
    return None


def pack_failure(pack: RuntimePack, hw: HwSummary, classes: Iterable[GpuClass] = ()) -> str | None:
    """Why ``pack`` cannot run here, as an error code (None = usable)."""
    if not pack.supports(hw.os, hw.arch):
        return "RUNTIME_UNSUPPORTED_PLATFORM"
    gpu = hw.primary_gpu
    accel = pack.accel
    if accel == "cpu":
        return None
    if accel == "metal" or accel == "coreml":
        return None if gpu and gpu.vendor == "apple" else "GPU_NOT_FOUND"
    if accel == "directml":
        return None if gpu and hw.os == "windows" else "GPU_NOT_FOUND"
    if gpu is None or gpu.vendor == "apple":
        return "GPU_NOT_FOUND"
    if accel == "vulkan":
        return None
    if accel == "rocm":
        return None if gpu.vendor == "amd" else "GPU_NOT_FOUND"
    if accel == "cuda":
        if gpu.vendor != "nvidia":
            return "GPU_NOT_FOUND"
        req = pack.requires
        cc = gpu_cc(gpu, classes)
        if cc and req.compute_capability_min and version_tuple(cc) < version_tuple(req.compute_capability_min):
            return "GPU_ARCH_UNSUPPORTED"
        if cc and req.cuda_architectures and cc.replace(".", "") not in req.cuda_architectures:
            return "GPU_ARCH_UNSUPPORTED"
        if gpu.driver and req.nvidia_driver_min and version_tuple(gpu.driver) < version_tuple(req.nvidia_driver_min):
            return "GPU_DRIVER_TOO_OLD"
        return None
    return "GPU_NOT_FOUND"


def candidate_packs(variant: Variant, runtimes: dict[str, RuntimePack]) -> list[RuntimePack]:
    rt_name = {"llama.cpp": "llama.cpp", "onnx": "onnx"}.get(variant.runtime, variant.runtime)
    globs = variant.requires.runtime_pack or ["*"]
    out = [
        p
        for p in runtimes.values()
        if p.provides.get("runtime") == rt_name
        and any(fnmatch.fnmatch(p.id, g) for g in globs)
        and (not variant.requires.accel_any or p.accel in variant.requires.accel_any)
        and not p.advanced
    ]
    return sorted(
        out, key=lambda p: (ACCEL_ORDER.index(p.accel) if p.accel in ACCEL_ORDER else 99, p.id), reverse=False
    )


def gpu_budget(gpu: Gpu, os: str) -> tuple[float, str]:
    """Usable VRAM for compat: by *total* capacity, not current free memory (06 §1.2)."""
    if gpu.unified:
        ws = gpu.budget_gb if gpu.budget_gb is not None else gpu.vram_total_gb * 0.7
        return max(0.0, ws - UNIFIED_RESERVE_GB), "metal" if gpu.budget_gb is not None else "estimated"
    if gpu.budget_gb is not None:
        return budget_gb(gpu.budget_gb, SAFETY, 0.0, 0.0), gpu.source
    return budget_gb(gpu.vram_total_gb, SAFETY, 0.0, DESKTOP_RESERVE_GB.get(os, 0.0)), "total"


def compat(
    model: LoadedModel,
    variant: str,
    hw: HwSummary,
    runtimes: dict[str, RuntimePack],
    *,
    gpu_classes: Iterable[GpuClass] = (),
    installed: Iterable[str] = (),
    consents: Iterable[str] = (),
    observed_need_gb: dict[str, float] | None = None,
    resident_small_gb: float = RESIDENT_SMALL_GB,
) -> Compat:
    spec = model.spec
    v = spec.variant(variant)
    key = f"{spec.id}@{v.id}"
    c = Compat(variant=key, level="ok")
    classes = list(gpu_classes)

    # 1. platform
    cands = candidate_packs(v, runtimes)
    if hw.os not in v.requires.os or not any(p.supports(hw.os, hw.arch) for p in cands):
        c.level = "blocked"
        c.reasons.append(_issue("RUNTIME_UNSUPPORTED_PLATFORM", os=hw.os, arch=hw.arch))
        return _prep(c, model, v, None, hw, installed, consents)

    # 2-3. accelerator and driver: first usable pack in preference order
    failures: list[tuple[RuntimePack, str]] = []
    chosen: RuntimePack | None = None
    for p in cands:
        f = pack_failure(p, hw, classes)
        if f is None:
            chosen = p
            break
        failures.append((p, f))
    if chosen is None:
        code = next((f for _, f in failures if f in ("GPU_DRIVER_TOO_OLD", "GPU_ARCH_UNSUPPORTED")), "GPU_NOT_FOUND")
        c.level = "blocked"
        c.reasons.append(_issue(code, pack=failures[0][0].id if failures else None))
        return _prep(c, model, v, None, hw, installed, consents)
    c.pack, c.accel = chosen.id, chosen.accel

    # 4. memory
    need = (observed_need_gb or {}).get(key)
    c.vram = {
        "need_gb": round(need if need is not None else v.vram_gb.need_gb, 2),
        "source": "observed" if need is not None else "estimated",
    }
    need = c.vram["need_gb"]
    if v.requires.min_ram_gb and hw.ram_gb and hw.ram_gb < v.requires.min_ram_gb:
        c.level = "blocked"
        c.reasons.append(_issue("RAM_INSUFFICIENT", need_gb=v.requires.min_ram_gb, have_gb=round(hw.ram_gb, 1)))
        return _prep(c, model, v, chosen, hw, installed, consents)
    gpu = hw.primary_gpu
    on_gpu = chosen.accel != "cpu" and gpu is not None
    if on_gpu:
        assert gpu is not None
        budget, bsrc = gpu_budget(gpu, hw.os)
        small = resident_small_gb if spec.kind in LARGE_KINDS else 0.0
        c.vram.update(
            {"budget_gb": round(budget, 2), "with_resident_gb": round(need + small, 2), "budget_source": bsrc}
        )
        decision = can_coload(need, small, budget)
        if decision == "sequential":
            c.level = "degraded"
            c.reasons.append(_issue("VRAM_SEQUENTIAL", need_gb=need, budget_gb=round(budget, 2)))
        elif decision == "blocked":
            ram_ok = hw.ram_gb >= need + 4 if hw.ram_gb else False
            if v.runtime == "llama.cpp" and not gpu.unified and ram_ok:
                c.level = "degraded"
                pct = round(100 * (need - budget) / need) if need else 0
                c.reasons.append(_issue("VRAM_OFFLOAD", model=key, pct=pct))
            else:
                c.level = "blocked"
                c.reasons.append(_issue("VRAM_INSUFFICIENT", need_gb=need, budget_gb=round(budget, 2)))
                return _prep(c, model, v, chosen, hw, installed, consents)
        free_now = budget - gpu.vram_used_external_gb
        want = need + (small if decision == "ok" else 0.0)
        c.fits_now = want <= free_now
        if not c.fits_now:
            c.fits_now_reason = "GPU_BUSY_EXTERNAL"
    else:
        ram_need = v.ram_gb if v.ram_gb is not None else need
        if hw.ram_gb and ram_need + 2 > hw.ram_gb:
            c.level = "blocked"
            c.reasons.append(_issue("RAM_INSUFFICIENT", need_gb=round(ram_need + 2, 1), have_gb=round(hw.ram_gb, 1)))
            return _prep(c, model, v, chosen, hw, installed, consents)
        c.fits_now = True
        # 5. speed: CPU only and 10,000 items would take > 24 h
        rate = CPU_ITEMS_PER_S.get(spec.kind)
        if rate is not None and 10_000 / rate > 24 * 3600:
            c.level = "degraded"
            c.reasons.append(_issue("PERF_CPU_ONLY_SLOW", hours_per_10k=round(10_000 / rate / 3600)))
    return _prep(c, model, v, chosen, hw, installed, consents)


def _prep(
    c: Compat,
    model: LoadedModel,
    v: Variant,
    pack: RuntimePack | None,
    hw: HwSummary,
    installed: Iterable[str],
    consents: Iterable[str],
) -> Compat:
    """6. preparation steps: never change the level (MDL-003)."""
    if pack is not None and not pack.builtin and pack.id not in hw.pack_ids:
        c.prep.append(_issue("RUNTIME_PACK_MISSING", pack=pack.id, size_bytes=pack.size_est))
    if c.variant not in set(installed):
        files = model.pinned_files(v.id)
        size = sum(f.size or 0 for f in files) or int((v.vram_gb.weights + v.vram_gb.mmproj) * 1e9)
        c.prep.append(_issue("MODEL_NOT_INSTALLED", size_bytes=size, estimated=not all(f.size for f in files)))
    if model.spec.license.requires_acceptance and model.spec.id not in set(consents):
        c.prep.append(_issue("LICENSE_CONSENT_REQUIRED", license=model.spec.license.spdx))
    return c
