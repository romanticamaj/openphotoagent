"""Resilient inference runner: OOM / crash degradation ladder (03 §4.3; INF-OOM-001..003).

* OOM → halve the batch down to 1, then lower ``max_pixels`` one step (re-preparing the inputs), finally
  ``InferenceError(OOM)`` → ``MODEL_OOM``.
* runtime crash → restart once (llama-server: ``--parallel`` halved); a second crash → next variant of the
  fallback chain. Every fallback is written to ``provenance`` (stored with the results).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from .types import BatchRequest, ImageInput, InferenceBackend, InferenceError, ItemOutput, LoadOptions


@dataclass
class Variant:
    key: str
    factory: Callable[[], InferenceBackend]
    pack: str | None = None


class ResilientRunner:
    def __init__(
        self,
        chain: list[Variant],
        pixel_ladder: list[int] | None = None,
        on_attempt: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if not chain:
            raise ValueError("empty variant chain")
        self.chain = list(chain)
        self.idx = 0
        self.backend: Any = None
        self.provenance: list[dict[str, Any]] = []
        self.batch_size: int | None = None
        self.pixel_ladder = list(pixel_ladder or [])
        self.on_attempt = on_attempt or (lambda _a: None)

    @property
    def current(self) -> Variant:
        return self.chain[self.idx]

    async def _ensure(self) -> Any:
        if self.backend is None:
            self.backend = self.current.factory()
            await self.backend.load(LoadOptions())
        return self.backend

    async def _drop(self) -> None:
        b, self.backend = self.backend, None
        if b is not None:
            close = getattr(b, "close", None)
            if close is not None:
                close()
            else:
                await b.unload()

    def choose(self, v: Variant) -> None:
        """Apply a user-chosen alternative (e.g. the doctor's "use Vulkan" fix) for the current slot."""
        self.provenance.append({"kind": "variant.chosen", "from": self.current.key, "to": v.key, "pack": v.pack})
        self.chain[self.idx] = v
        self.backend = None

    async def _run_items(self, req: BatchRequest, items: list[ImageInput], side: int | None) -> list[ItemOutput]:
        b = await self._ensure()
        out: list[ItemOutput] = []
        bs = min(self.batch_size or len(items), len(items)) or 1
        i = 0
        while i < len(items):
            chunk = items[i : i + bs]
            self.on_attempt({"batch": len(chunk), "max_side": side, "variant": self.current.key})
            try:
                out += await b.run_batch(replace(req, items=chunk))
            except InferenceError as e:
                if e.code == "OOM" and bs > 1:
                    bs = max(1, bs // 2)
                    self.batch_size = bs
                    continue
                raise
            i += len(chunk)
        self.batch_size = bs
        return out

    async def run(
        self, req: BatchRequest, reprepare: Callable[[int], list[ImageInput]] | None = None
    ) -> list[ItemOutput]:
        items = list(req.items)
        ladder = self.pixel_ladder
        step = 0
        side = ladder[0] if ladder else None
        while True:
            try:
                return await self._run_items(req, items, side)
            except InferenceError as e:
                if e.code == "RUNTIME" and e.retryable:
                    b = self.backend
                    if b is not None and hasattr(b, "restart") and getattr(b, "restarts", 0) == 0:
                        await b.restart()
                        self.provenance.append(
                            {
                                "kind": "runtime.restarted",
                                "variant": self.current.key,
                                "parallel": getattr(b, "parallel", None),
                            }
                        )
                        continue
                    if self.idx + 1 < len(self.chain):
                        await self._drop()
                        prev, pack = self.current.key, self.current.pack
                        self.idx += 1
                        self.provenance.append(
                            {
                                "kind": "variant.fallback",
                                "from": prev,
                                "to": self.current.key,
                                "pack": pack,
                                "reason": "RUNTIME_CRASHED",
                            }
                        )
                        continue
                    self.provenance.append(
                        {"kind": "runtime.crashed_twice", "variant": self.current.key, "pack": self.current.pack}
                    )
                    await self._drop()
                    raise
                if e.code == "OOM" and reprepare is not None and step + 1 < len(ladder):
                    step += 1
                    side = ladder[step]
                    items = reprepare(side)
                    self.batch_size = None
                    self.provenance.append({"kind": "max_pixels.reduced", "max_side": side})
                    continue
                raise
