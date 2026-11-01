"""Resource policy: pause on battery / GPU contention / when not idle (08 §6, C-30; 06 §5.5.7).

The worker consults the policy before leasing heavy work and reports ``paused_reason`` in its heartbeat.
Search and other light requests are never paused. Providers are swappable (FakePower/FakeIdle/FakeGpu in tests;
OS probes in the desktop app).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

HEAVY = {"job.label", "scan", "thumbnail", "reindex", "face.reembed", "model.load", "benchmark.run"}
FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "hw"
CONTENTION_MB = 2048  # external VRAM use that counts as "GPU busy" (03 §4.1 reserved_external)


class FakePower:
    def __init__(self, on_ac: bool = True) -> None:
        self.on_ac = on_ac


class FakeIdle:
    def __init__(self, seconds: float = 0) -> None:
        self.seconds = seconds


class FakeGpu:
    def __init__(self, external_used_mb: int = 0, fullscreen: bool = False) -> None:
        self.external_used_mb = external_used_mb
        self.fullscreen = fullscreen

    @classmethod
    def from_profile(cls, name: str) -> FakeGpu:
        """Parse the raw probe outputs of a fixtures/hw profile (the doctor parses the same data)."""
        doc = json.loads(
            "\n".join(
                ln for ln in (FIXTURES / f"{name}.yaml").read_text().splitlines() if not ln.lstrip().startswith("#")
            )
        )
        probes = doc["probes"]
        xml = probes.get("nvidia_smi_xml") or ""
        used = sum(int(m) for m in re.findall(r"<used_memory>(\d+) MiB</used_memory>", xml))
        return cls(external_used_mb=used, fullscreen=bool((probes.get("os_state") or {}).get("fullscreen_d3d")))


class ResourcePolicy:
    def __init__(
        self, power: Any = None, gpu: Any = None, idle: Any = None, settings: dict[str, Any] | None = None
    ) -> None:
        self.power, self.gpu, self.idle = power, gpu, idle
        self.settings = {
            "pause_on_battery": True,
            "pause_on_contention": True,
            "only_when_idle": False,
            "idle_after_s": 300,
            **(settings or {}),
        }
        self.device = "gpu"
        self._overrides: set[str] = set()

    def override(self, what: str) -> None:
        """User CTAs: "仍要繼續" (continue_anyway), "改用 CPU" (use_cpu), "現在就處理" (run_now)."""
        self._overrides.add(what)
        if what == "use_cpu":
            self.device = "cpu"

    def paused_reason(self) -> str | None:
        s = self.settings
        if (
            s["pause_on_battery"]
            and self.power is not None
            and not self.power.on_ac
            and "continue_anyway" not in self._overrides
        ):
            return "PAUSED_ON_BATTERY"
        if (
            s["pause_on_contention"]
            and self.gpu is not None
            and self.device == "gpu"
            and (self.gpu.fullscreen or self.gpu.external_used_mb >= CONTENTION_MB)
        ):
            return "PAUSED_GPU_CONTENDED"
        if (
            s["only_when_idle"]
            and self.idle is not None
            and self.idle.seconds < s["idle_after_s"]
            and "run_now" not in self._overrides
        ):
            return "PAUSED_NOT_IDLE"
        return None

    def allows(self, op_type: str) -> bool:
        return op_type not in HEAVY or self.paused_reason() is None
