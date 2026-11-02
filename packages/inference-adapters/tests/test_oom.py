import asyncio
import io

import pytest
from opagent_adapters.llamacpp import LlamaCppServerAdapter
from opagent_adapters.testing import FAKE_RUNTIME
from opagent_doctor.advice import advise_runtime
from opagent_inference.fake import FakeBackend
from opagent_inference.service import ResilientRunner, Variant
from opagent_inference.types import BatchRequest, ImageInput, InferenceError
from PIL import Image


def _jpegs(n, side=64):
    out = []
    for k in range(n):
        buf = io.BytesIO()
        Image.new("RGB", (side, side), (k * 9 % 256, 30, 60)).save(buf, "JPEG")
        out.append(ImageInput(f"i{k}", f"h{k}", buf.getvalue()))
    return out


def _llama(state, crash_first_n):
    return LlamaCppServerAdapter(
        FAKE_RUNTIME, "m.gguf", None, parallel=4, categories=["other"],
        extra_args=["--fake-state", str(state), "--crash-first-n", str(crash_first_n), "--fake-load-s", "0.05"],
    )  # fmt: skip


@pytest.mark.integration
@pytest.mark.tc("INF-OOM-001")
def test_inf_oom_001_restart_once_with_parallel_halved(tmp_path):
    a = _llama(tmp_path / "s1.json", crash_first_n=1)
    runner = ResilientRunner([Variant("vlm@q5", lambda: a)])
    try:
        out = asyncio.run(runner.run(BatchRequest(task_type="classify", items=_jpegs(2))))
        assert len(out) == 2
        assert a.restarts == 1 and a.parallel == 2
        assert [e["kind"] for e in runner.provenance] == ["runtime.restarted"]
    finally:
        a.close()


@pytest.mark.integration
@pytest.mark.tc("INF-OOM-001")
def test_inf_oom_001_second_crash_falls_back_to_next_variant(tmp_path):
    a = _llama(tmp_path / "s2.json", crash_first_n=2)
    b = FakeBackend(seed=1)
    runner = ResilientRunner([Variant("vlm@q5_k_m", lambda: a), Variant("vlm@q4_k_m", lambda: b)])
    try:
        out = asyncio.run(runner.run(BatchRequest(task_type="classify", items=_jpegs(3))))
        assert len(out) == 3 and runner.current.key == "vlm@q4_k_m"
        assert [e["kind"] for e in runner.provenance] == ["runtime.restarted", "variant.fallback"]
        assert runner.provenance[-1]["from"] == "vlm@q5_k_m" and runner.provenance[-1]["to"] == "vlm@q4_k_m"
    finally:
        a.close()


@pytest.mark.unit
@pytest.mark.tc("INF-OOM-002")
def test_inf_oom_002_batch_halving_then_pixels_then_model_oom():
    fb = FakeBackend()
    fb.inject("OOM", times=10**6)
    sizes = []
    runner = ResilientRunner([Variant("embed@fp16", lambda: fb)], pixel_ladder=[768, 512, 384], on_attempt=sizes.append)
    with pytest.raises(InferenceError) as e:
        asyncio.run(
            runner.run(BatchRequest(task_type="embed", items=_jpegs(16)), reprepare=lambda side: _jpegs(16, side))
        )
    assert e.value.code == "OOM" and e.value.error_code == "MODEL_OOM"
    assert [s["batch"] for s in sizes][:5] == [16, 8, 4, 2, 1]
    assert sizes[5]["max_side"] == 512 and sizes[5]["batch"] == 16
    assert any(p["kind"] == "max_pixels.reduced" for p in runner.provenance)


@pytest.mark.unit
@pytest.mark.tc("INF-OOM-002")
def test_inf_oom_002_recovers_at_smaller_batch():
    fb = FakeBackend()
    fb.inject("OOM", times=10**6, on_batch_size_at_least=5)
    runner = ResilientRunner([Variant("embed@fp16", lambda: fb)])
    out = asyncio.run(runner.run(BatchRequest(task_type="embed", items=_jpegs(16))))
    assert len(out) == 16 and runner.batch_size == 4


@pytest.mark.integration
@pytest.mark.tc("INF-OOM-003")
def test_inf_oom_003_cuda_crashes_twice_suggest_vulkan(tmp_path):
    cuda = _llama(tmp_path / "s3.json", crash_first_n=2)
    vulkan = FakeBackend(seed=2)
    runner = ResilientRunner([Variant("vlm@q5_k_m", lambda: cuda, pack="llamacpp-cuda13")])
    try:
        with pytest.raises(InferenceError) as e:
            asyncio.run(runner.run(BatchRequest(task_type="classify", items=_jpegs(2))))
        assert e.value.code == "RUNTIME"
        adv = advise_runtime(runner.provenance)
        assert adv is not None and adv.code == "RUNTIME_CRASHED"
        assert adv.fixes[0] == {"type": "choose_alternative", "pack": "llamacpp-vulkan"}
        runner.choose(Variant("vlm@q5_k_m", lambda: vulkan, pack=adv.fixes[0]["pack"]))  # the user clicks the fix
        out = asyncio.run(runner.run(BatchRequest(task_type="classify", items=_jpegs(2))))
        assert len(out) == 2 and runner.current.pack == "llamacpp-vulkan"
    finally:
        cuda.close()
