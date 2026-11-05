"""ONNX Runtime embedding adapter for SigLIP2-style dual encoders (03 §2.4, 08 §3.2; INF-CONF).

Execution providers are chosen by the runtime recommender (DirectML on Windows, CoreML on macOS, CPU elsewhere,
CUDA with the ``ort-cuda`` pack). Images arrive as encoded bytes and are decoded in memory.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import threading
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import onnxruntime as ort
from opagent_core.operation import NullReporter, OperationReporter
from opagent_inference.types import (
    BackendInfo,
    BatchRequest,
    HealthStatus,
    InferenceError,
    ItemOutput,
    LoadOptions,
    ModelUnavailable,
    PreferredInput,
)
from PIL import Image

from .openai_compat import check_items

Tokenizer = Callable[[Sequence[str]], np.ndarray]


def map_ort_error(e: Exception) -> InferenceError:
    msg = str(e)
    low = msg.lower()
    if "allocate" in low or "out of memory" in low:
        return InferenceError("OOM", True, msg)
    if "terminate flag" in low:
        return InferenceError("TIMEOUT", True, msg)
    if type(e).__name__ in ("InvalidArgument", "InvalidGraph") or "invalid" in low or isinstance(e, ValueError):
        return InferenceError("BAD_INPUT", False, msg)
    return InferenceError("RUNTIME", True, msg)


class OrtEmbedAdapter:
    adapter_key = "onnx"

    def __init__(
        self,
        vision_model: str | bytes,
        text_model: str | bytes | None,
        dim: int,
        *,
        input_size: int = 384,
        providers: Sequence[str] = ("CPUExecutionProvider",),
        tokenizer: Tokenizer | None = None,
        fingerprint: str | None = None,
        mean: float = 0.5,
        std: float = 0.5,
        max_batch: int = 32,
    ) -> None:
        self.vision_model, self.text_model = vision_model, text_model
        self.dim = dim
        self.input_size = input_size
        self.providers = list(providers)
        self.tokenizer = tokenizer
        src = vision_model if isinstance(vision_model, bytes) else vision_model.encode()
        self.fingerprint = fingerprint or "ort-" + hashlib.sha256(src[:4096] + str(dim).encode()).hexdigest()[:12]
        self.mean, self.std = mean, std
        self.max_batch = max_batch
        self.vision: ort.InferenceSession | None = None
        self.text: ort.InferenceSession | None = None
        self.loaded = False
        self.terminations = 0

    def describe(self) -> BackendInfo:
        return BackendInfo(
            adapter_key=self.adapter_key,
            model_fingerprint=self.fingerprint,
            task_types=("embed",),
            preferred_input=PreferredInput(max_side=self.input_size, format="jpeg"),
            max_batch=self.max_batch,
            est_vram_mb=0,
            supports_structured_output=False,
            embedding_dim=self.dim,
        )

    async def load(self, opts: LoadOptions, reporter: OperationReporter | None = None) -> None:
        rep = reporter or NullReporter()
        if self.loaded:
            return
        rep.start("model.load", 2, "steps")
        rep.phase("vision_encoder")
        self.vision = ort.InferenceSession(self.vision_model, providers=self.providers)
        rep.progress(1, 2)
        rep.phase("text_encoder")
        if self.text_model is not None:
            self.text = ort.InferenceSession(self.text_model, providers=self.providers)
        rep.progress(2, 2)
        self.loaded = True
        rep.finish("succeeded")

    async def unload(self) -> None:
        self.vision = self.text = None
        self.loaded = False

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=self.loaded, vram_used_mb=0)  # CPU/DirectML/CoreML: no NVML reading

    def _pixels(self, data: bytes) -> np.ndarray:
        try:
            im = Image.open(io.BytesIO(data))
            rgb = im.convert("RGB").resize((self.input_size, self.input_size), Image.Resampling.BICUBIC)
        except Exception as e:
            raise InferenceError("BAD_INPUT", False, f"cannot decode image: {e}") from None
        arr = np.asarray(rgb, dtype=np.float32) / 255.0
        return ((arr - self.mean) / self.std).transpose(2, 0, 1)

    @staticmethod
    def _embedding_output(sess: ort.InferenceSession) -> list[str] | None:
        """Exports differ: ours has one output; the public one puts last_hidden_state first (INF-LOCAL-004)."""
        return ["pooler_output"] if any(o.name == "pooler_output" for o in sess.get_outputs()) else None

    def _run(self, sess: ort.InferenceSession, feeds: dict[str, np.ndarray], ro: ort.RunOptions) -> np.ndarray:
        return np.asarray(sess.run(self._embedding_output(sess), feeds, ro)[0], dtype=np.float32)

    @staticmethod
    def _l2(x: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(x, axis=1, keepdims=True)
        return x / np.where(n == 0, 1, n)

    async def run_batch(self, req: BatchRequest) -> list[ItemOutput]:
        items = check_items(list(req.items))
        if not items:
            return []
        if not self.loaded or self.vision is None:
            raise ModelUnavailable("onnx session not loaded")
        if req.task_type != "embed":
            raise InferenceError("BAD_INPUT", False, f"task {req.task_type} not supported by embedding adapter")
        batch = np.stack([self._pixels(it.data) for it in items])
        sess = self.vision
        ro = ort.RunOptions()
        done = threading.Event()
        result: dict[str, Any] = {}

        def work() -> None:
            try:
                result["v"] = self._run(sess, {sess.get_inputs()[0].name: batch}, ro)
            except Exception as e:
                result["e"] = e
            finally:
                done.set()

        threading.Thread(target=work, daemon=True).start()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + req.deadline_s
        while not done.is_set():
            if loop.time() > deadline:
                ro.terminate = True  # cancels the in-flight ORT run
                self.terminations += 1
                await loop.run_in_executor(None, done.wait, 5)
                raise InferenceError("TIMEOUT", True, f"deadline {req.deadline_s}s exceeded")
            await asyncio.sleep(0.01)
        if "e" in result:
            raise map_ort_error(result["e"])
        vecs = self._l2(result["v"].reshape(len(items), -1))
        if vecs.shape[1] != self.dim:
            raise InferenceError("RUNTIME", False, f"embedding dim {vecs.shape[1]} != registry dim {self.dim}")
        return [
            ItemOutput(item_id=it.item_id, embedding=[float(x) for x in v]) for it, v in zip(items, vecs, strict=True)
        ]

    async def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        if self.text is None or self.tokenizer is None:
            raise ModelUnavailable("text encoder not loaded")
        ids = self.tokenizer(texts)
        out = self.text.run(self._embedding_output(self.text), {self.text.get_inputs()[0].name: ids})[0]
        return self._l2(np.asarray(out, dtype=np.float32))
