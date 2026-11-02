"""Conformance harness builders for the real adapters (09a §6.2). Test-support only."""

from __future__ import annotations

import sys
import time
from typing import Any

import httpx
import numpy as np

from .llamacpp import LlamaCppServerAdapter
from .openai_compat import OpenAICompatAdapter
from .ort_embed import OrtEmbedAdapter

FAKE_RUNTIME = [sys.executable, "-m", "opagent_testing.fake_llama_server"]


def _fault(b: LlamaCppServerAdapter, body: dict[str, Any]) -> None:
    httpx.post(b.base_url + "/_fault", json=body, timeout=5)


def _state(b: LlamaCppServerAdapter) -> dict[str, Any]:
    return dict(httpx.get(b.base_url + "/_state", timeout=5).json())


def _cancelled(b: LlamaCppServerAdapter) -> bool:
    for _ in range(100):  # the server notices the closed socket within its 20 ms poll
        if _state(b)["cancelled"] > 0:
            return True
        time.sleep(0.02)  # noqa: TID251 - polling a subprocess
    return False


def make_llama(**kw: Any) -> LlamaCppServerAdapter:
    return LlamaCppServerAdapter(
        FAKE_RUNTIME, "fake-model.gguf", "fake-mmproj.gguf", parallel=4, categories=["other"], **kw
    )


def llamacpp_harness(harness_cls: Any, sibling: Any = None) -> Any:
    return harness_cls(
        "llamacpp-fakeruntime",
        lambda h: make_llama(),
        lambda b, code: _fault(b, {code: 1}),
        lambda b, s: _fault(b, {"latency_s": s}),
        _cancelled,
        embedding_dim=None,
        sibling_fn=sibling,
    )


def managed_harness(harness_cls: Any, flavor: str) -> Any:
    from .managed import MlxServerAdapter, VllmServerAdapter

    cls, plat = (VllmServerAdapter, "linux") if flavor == "vllm" else (MlxServerAdapter, "darwin-arm64")

    def make(h: Any) -> Any:
        return cls([*FAKE_RUNTIME, "--flavor", flavor], "fake-model", parallel=4, categories=["other"], platform=plat)

    return harness_cls(
        f"{flavor}-fakeruntime",
        make,
        lambda b, code: _fault(b, {code: 1}),
        lambda b, s: _fault(b, {"latency_s": s}),
        _cancelled,
        embedding_dim=None,
    )


def openai_sibling(h: Any) -> Any:
    """The same running FakeRuntime seen through the generic OpenAI-compatible adapter (INF-CONF-010)."""
    live = next((b for b in h.made if isinstance(b, LlamaCppServerAdapter)), None)
    if live is None:
        return None
    return OpenAICompatAdapter(live.base_url, model=live.model, categories=["other"])


# ---- tiny ONNX dual encoder (random weights) ----
DIM, SIZE, VOCAB, SEQ = 8, 32, 64, 16


def tiny_onnx_models() -> tuple[bytes, bytes]:
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    rng = np.random.default_rng(0)
    w = numpy_helper.from_array(rng.normal(0, 0.05, (3 * SIZE * SIZE, DIM)).astype(np.float32), "W")
    shape = numpy_helper.from_array(np.array([0, -1], dtype=np.int64), "shape")
    vision = helper.make_model(
        helper.make_graph(
            [
                helper.make_node("Reshape", ["pixel_values", "shape"], ["flat"]),
                helper.make_node("MatMul", ["flat", "W"], ["image_embeds"]),
            ],
            "tiny_vision",
            [helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT, ["N", 3, SIZE, SIZE])],
            [helper.make_tensor_value_info("image_embeds", TensorProto.FLOAT, ["N", DIM])],
            [w, shape],
        ),
        opset_imports=[helper.make_opsetid("", 17)],
    )
    emb = numpy_helper.from_array(rng.normal(0, 1, (VOCAB, DIM)).astype(np.float32), "E")
    axes = numpy_helper.from_array(np.array([1], dtype=np.int64), "axes")
    text = helper.make_model(
        helper.make_graph(
            [
                helper.make_node("Gather", ["E", "input_ids"], ["tok"]),
                helper.make_node("ReduceMean", ["tok", "axes"], ["text_embeds"], keepdims=0),
            ],
            "tiny_text",
            [helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["N", SEQ])],
            [helper.make_tensor_value_info("text_embeds", TensorProto.FLOAT, ["N", DIM])],
            [emb, axes],
        ),
        opset_imports=[helper.make_opsetid("", 18)],
    )
    for m in (vision, text):
        m.ir_version = 9
        onnx.checker.check_model(m)
    return vision.SerializeToString(), text.SerializeToString()


def toy_tokenizer(texts: Any) -> np.ndarray:
    out = np.zeros((len(texts), SEQ), dtype=np.int64)
    for i, t in enumerate(texts):
        ids = [b % VOCAB for b in t.encode()][:SEQ]
        out[i, : len(ids)] = ids
    return out


def make_ort() -> OrtEmbedAdapter:
    v, t = tiny_onnx_models()
    return OrtEmbedAdapter(v, t, DIM, input_size=SIZE, tokenizer=toy_tokenizer)


class _Patched:
    def __init__(self, adapter: OrtEmbedAdapter) -> None:
        self.adapter = adapter
        self.fault: str | None = None
        self.latency = 0.0
        self.saw_terminate = False
        self.orig = adapter._run
        adapter._run = self.run  # type: ignore[method-assign]

    def run(self, sess: Any, feeds: Any, ro: Any) -> Any:
        from onnxruntime.capi.onnxruntime_pybind11_state import Fail, InvalidArgument, RuntimeException

        fault, self.fault = self.fault, None
        if fault == "OOM":
            raise RuntimeException("Failed to allocate memory for requested buffer of size 1073741824")
        if fault == "BAD_INPUT":
            raise InvalidArgument("Got invalid dimensions for input: pixel_values")
        if fault == "RUNTIME":
            raise Fail("Non-zero status code returned while running MatMul node")
        if fault == "TIMEOUT":
            raise Fail("Exiting due to terminate flag being set to true.")
        end = time.monotonic() + self.latency  # noqa: TID251 - simulated slow kernel honouring RunOptions.terminate
        while time.monotonic() < end:  # noqa: TID251
            if ro.terminate:
                self.saw_terminate = True
                raise Fail("Exiting due to terminate flag being set to true.")
            time.sleep(0.01)  # noqa: TID251
        return self.orig(sess, feeds, ro)


def ort_harness(harness_cls: Any) -> Any:
    def make(h: Any) -> OrtEmbedAdapter:
        a = make_ort()
        a._patch = _Patched(a)  # type: ignore[attr-defined]
        return a

    def inject(b: Any, code: str) -> None:
        b._patch.fault = code

    def slow(b: Any, s: float) -> None:
        b._patch.latency = s

    return harness_cls("ort-tiny", make, inject, slow, lambda b: b._patch.saw_terminate, embedding_dim=DIM)
