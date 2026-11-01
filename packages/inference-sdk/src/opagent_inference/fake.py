"""FakeInferenceBackend — deterministic test double (docs/09 §3.1).

Outputs are derived from ``sha256(content_hash + seed)``; when an item carries
``labels`` (from ``fixtures/photos/labels.json``) those are used, so E2E runs have
predictable organize results. Never touches the filesystem or the clock.
"""

from __future__ import annotations

import hashlib

from typing import Any

from .types import (
    InferenceError,
    BackendInfo,
    BatchRequest,
    HealthStatus,
    ItemOutput,
    LoadOptions,
    ModelUnavailable,
    PreferredInput,
)

DEFAULT_CATEGORIES = (
    "people",
    "pets",
    "food",
    "landscape",
    "city",
    "documents",
    "screenshots",
    "events",
    "travel",
    "nature",
    "vehicles",
    "objects",
)


class FakeBackend:
    adapter_key = "fake"

    def __init__(self, seed: int = 0, est_vram_mb: int = 512, embedding_dim: int = 16, clock: Any = None,
                 latency_ms_per_item: float = 0.0) -> None:
        self.seed = seed
        self.est_vram_mb = est_vram_mb
        self.embedding_dim = embedding_dim
        self.loaded = False
        self.load_count = 0
        self._calls: list[dict[str, object]] = []
        self._faults: list[dict[str, Any]] = []

    def describe(self) -> BackendInfo:
        fp = hashlib.sha256(f"fake:{self.seed}:{self.embedding_dim}".encode()).hexdigest()[:16]
        return BackendInfo(
            adapter_key=self.adapter_key,
            model_fingerprint=f"fake-{fp}",
            task_types=("classify", "caption", "tag", "embed"),
            preferred_input=PreferredInput(max_side=512, format="rgb"),
            max_batch=32,
            est_vram_mb=self.est_vram_mb,
            supports_structured_output=True,
            embedding_dim=self.embedding_dim,
        )

    async def load(self, opts: LoadOptions) -> None:
        if not self.loaded:
            self.loaded = True
            self.load_count += 1

    async def unload(self) -> None:
        self.loaded = False

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=self.loaded, vram_used_mb=self.est_vram_mb if self.loaded else 0)

    def inject(self, code: str, times: int = 10**9, on_batch_size_at_least: int = 1) -> None:
        """Fail the next ``times`` batches of at least ``on_batch_size_at_least`` items (09 §3.1 fault injection)."""
        self._faults.append({"code": code, "times": times, "min": on_batch_size_at_least})

    def calls(self, task: str | None = None) -> list[dict[str, object]]:
        return [c for c in self._calls if task is None or c["task"] == task]

    async def run_batch(self, req: BatchRequest) -> list[ItemOutput]:
        if not req.items:
            return []
        if not self.loaded:
            raise ModelUnavailable("fake backend not loaded")
        for f in self._faults:
            if f["times"] > 0 and len(req.items) >= f["min"]:
                f["times"] -= 1
                code = f["code"]
                raise InferenceError(code, retryable=code != "BAD_INPUT", message=f"injected {code}")
        self._calls.append({"task": req.task_type, "n": len(req.items)})
        cats = tuple(req.variables.get("categories") or DEFAULT_CATEGORIES)
        out: list[ItemOutput] = []
        for it in req.items:
            h = hashlib.sha256(f"{it.content_hash}:{self.seed}".encode()).digest()
            cat = str(it.labels.get("category") or cats[h[0] % len(cats)])
            conf = float(it.labels.get("confidence", 0.5 + (h[1] / 255) * 0.5))
            out.append(
                ItemOutput(
                    item_id=it.item_id,
                    caption=str(it.labels.get("caption") or f"a {cat} photo"),
                    tags=[(str(t), 0.9) for t in it.labels.get("tags", [cat])],
                    category=cat,
                    confidence=conf,
                    raw={"backend": "fake"},
                    usage={"latency_ms": 0},
                )
            )
        return out
