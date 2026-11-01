"""FakeInferenceBackend — deterministic test double (docs/09 §3.1)."""

from __future__ import annotations

from .types import BackendInfo, BatchRequest, HealthStatus, ItemOutput, LoadOptions, PreferredInput


class FakeBackend:
    def __init__(self, seed: int = 0) -> None:
        self.seed = seed

    def describe(self) -> BackendInfo:
        return BackendInfo("", "", ("classify",), PreferredInput(0, "rgb"), 0, 0, False)

    async def load(self, opts: LoadOptions) -> None:
        return None

    async def unload(self) -> None:
        return None

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=False, vram_used_mb=0)

    async def run_batch(self, req: BatchRequest) -> list[ItemOutput]:
        return []
