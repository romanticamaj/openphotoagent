"""Hardware summary produced by the doctor's probes (06 §3, 09 §3.4). Pure data, no I/O."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Literal

Vendor = Literal["nvidia", "amd", "intel", "apple", "other"]


@dataclass(frozen=True)
class Gpu:
    vendor: str
    name: str
    vram_total_gb: float = 0.0
    vram_used_external_gb: float = 0.0
    compute_capability: str | None = None
    driver: str | None = None
    unified: bool = False
    budget_gb: float | None = None  # OS-reported budget (DXGI Budget, VK_EXT_memory_budget, Metal working set)
    source: str = "estimated"  # nvml | dxgi | vulkan | metal | estimated


@dataclass(frozen=True)
class HwSummary:
    os: str  # windows | macos | linux
    arch: str  # x86_64 | arm64
    ram_gb: float = 0.0
    cpu_count: int = 1
    gpus: tuple[Gpu, ...] = ()
    installed_packs: tuple[tuple[str, str, str | None], ...] = ()  # (id, version, cuda_version)
    in_container: bool = False
    on_battery: bool = False
    fullscreen: bool = False

    @property
    def primary_gpu(self) -> Gpu | None:
        return max(self.gpus, key=lambda g: g.vram_total_gb, default=None)

    @property
    def pack_ids(self) -> set[str]:
        return {p[0] for p in self.installed_packs}

    def with_packs(self, packs: tuple[str, ...]) -> HwSummary:
        return replace(self, installed_packs=(*self.installed_packs, *((p, "", None) for p in packs)))

    def with_gpu_driver(self, driver: str) -> HwSummary:
        return replace(self, gpus=tuple(replace(g, driver=driver) for g in self.gpus))

    def digest(self) -> str:
        """Hardware part of compat's ``inputs_hash`` (06 §1.2); excludes volatile state such as current usage."""
        stable = {
            "os": self.os,
            "arch": self.arch,
            "ram": round(self.ram_gb),
            "gpus": [{k: v for k, v in asdict(g).items() if k not in ("vram_used_external_gb",)} for g in self.gpus],
            "packs": sorted(self.installed_packs, key=lambda p: (p[0], p[1], p[2] or "")),
        }
        return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()[:16]


def version_tuple(v: str | None) -> tuple[int, ...]:
    if not v:
        return ()
    out = []
    for part in v.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)
