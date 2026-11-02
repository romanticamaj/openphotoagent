"""Backends that must pass the conformance suite (09a §6.2). Each harness knows how to build its backend and how
to inject faults (OOM/TIMEOUT/BAD_INPUT/RUNTIME), slow it down and tell whether a request was cancelled."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from opagent_inference.fake import FakeBackend
from opagent_inference.types import InferenceBackend


@dataclass
class Harness:
    name: str
    make_fn: Callable[[Harness], InferenceBackend]
    inject_fn: Callable[[Any, str], None]
    slow_fn: Callable[[Any, float], None]
    cancelled_fn: Callable[[Any], bool]
    embedding_dim: int | None = None
    has_gpu: bool = False
    sibling_fn: Callable[[Harness], InferenceBackend | None] | None = None
    made: list[Any] = field(default_factory=list)

    def make(self) -> Any:
        b = self.make_fn(self)
        self.made.append(b)
        return b

    def inject(self, b: Any, code: str) -> None:
        self.inject_fn(b, code)

    def slow(self, b: Any, seconds: float) -> None:
        self.slow_fn(b, seconds)

    def was_cancelled(self, b: Any) -> bool:
        return self.cancelled_fn(b)

    def sibling(self) -> Any:
        if self.sibling_fn is None:
            return None
        b = self.sibling_fn(self)
        if b is not None:
            self.made.append(b)
        return b

    def cleanup(self) -> None:
        for b in self.made:
            close = getattr(b, "close", None)
            if close:
                close()
            elif getattr(b, "loaded", False):
                asyncio.run(b.unload()) if not _running_loop() else None
        self.made.clear()


def _running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _fake_slow(b: FakeBackend, s: float) -> None:
    b.latency_s = s


HARNESSES: dict[str, Harness] = {
    "fake": Harness(
        "fake",
        lambda h: FakeBackend(seed=0),
        lambda b, code: b.inject(code, times=1),
        _fake_slow,
        lambda b: b.cancelled > 0,
        embedding_dim=16,
    ),
}

# real adapters; llama.cpp is exercised through FakeRuntime (opagent_testing.fake_llama_server)
from opagent_adapters.testing import llamacpp_harness, openai_sibling, ort_harness  # noqa: E402

HARNESSES["llamacpp-fakeruntime"] = llamacpp_harness(Harness, sibling=openai_sibling)
HARNESSES["ort-tiny"] = ort_harness(Harness)
# v0.4 advanced packs, through FakeRuntime flavours that enforce each runtime's request style (RT-VLLM-001, RT-MLX-001)
from opagent_adapters.testing import managed_harness  # noqa: E402

HARNESSES["vllm-fakeruntime"] = managed_harness(Harness, "vllm")
HARNESSES["mlx-fakeruntime"] = managed_harness(Harness, "mlx")
from opagent_adapters.testing import torch_harness  # noqa: E402

HARNESSES["torch-fakepipeline"] = torch_harness(Harness)  # RT-TORCH-001

FACTORIES: dict[str, Callable[[], InferenceBackend]] = {
    name: (lambda h=h: h.make_fn(h)) for name, h in HARNESSES.items()
}
