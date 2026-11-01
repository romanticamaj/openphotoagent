"""Resource policy: battery / GPU contention / idle (08 §6, C-30)."""

from __future__ import annotations

from typing import Any


class FakePower:
    def __init__(self, on_ac: bool = True) -> None:
        self.on_ac = on_ac


class FakeIdle:
    def __init__(self, seconds: float = 0) -> None:
        self.seconds = seconds


class FakeGpu:
    def __init__(self) -> None:
        self.external_used_mb = 0
        self.fullscreen = False

    @classmethod
    def from_profile(cls, name: str) -> FakeGpu:
        return cls()


class ResourcePolicy:
    def __init__(self, power: Any = None, gpu: Any = None, idle: Any = None, settings: dict[str, Any] | None = None):
        self.device = "gpu"

    def paused_reason(self) -> str | None:
        return "X"

    def allows(self, op_type: str) -> bool:
        return False

    def override(self, what: str) -> None:
        pass
