"""Hardware summary produced by the doctor's probes (06 §3, 09 §3.4). Pure data."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Gpu:
    vendor: str
    name: str
    vram_total_gb: float = 0.0


@dataclass(frozen=True)
class HwSummary:
    os: str
    arch: str
    ram_gb: float = 0.0
    cpu_count: int = 1
    gpus: tuple[Gpu, ...] = ()
    installed_packs: tuple[tuple[str, str, str | None], ...] = ()
