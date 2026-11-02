"""RT-VLLM-001 / RT-MLX-001 (09b §7.1): the vLLM and MLX server adapters. The full INF-CONF suite runs them through
FakeRuntime flavours (conformance/backends.py); this file checks what is specific to each runtime."""

import io

import pytest
from opagent_adapters.managed import MlxServerAdapter, VllmServerAdapter
from opagent_adapters.testing import FAKE_RUNTIME
from opagent_inference.types import BatchRequest, ImageInput, LoadOptions, UnsupportedPlatform
from PIL import Image


def _img():
    b = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 10, 10)).save(b, "JPEG")
    return [ImageInput(item_id="a", content_hash="h", data=b.getvalue())]


@pytest.mark.contract
@pytest.mark.tc("RT-VLLM-001")
async def test_vllm_uses_guided_json_and_vllm_flags():
    a = VllmServerAdapter(
        [*FAKE_RUNTIME, "--flavor", "vllm"], "Qwen/Qwen3-VL-8B-Instruct", categories=["cat", "dog"], platform="linux"
    )
    args = a.args()
    assert "--model" in args and "--max-num-seqs" in args and "--jinja" not in args
    try:
        await a.load(LoadOptions())
        out = await a.run_batch(BatchRequest(task_type="classify", items=_img()))
        assert out[0].category in ("cat", "dog")  # the fake refuses response_format json_schema: guided_json was used
        assert a.describe().adapter_key == "vllm"
    finally:
        a.close()


@pytest.mark.contract
@pytest.mark.tc("RT-MLX-001")
async def test_mlx_self_reported_confidence():
    a = MlxServerAdapter(
        [*FAKE_RUNTIME, "--flavor", "mlx"],
        "mlx-community/Qwen3-VL-4B",
        categories=["cat", "dog"],
        platform="darwin-arm64",
    )
    try:
        await a.load(LoadOptions())
        out = await a.run_batch(BatchRequest(task_type="classify", items=_img()))
        assert out[0].category in ("cat", "dog", "other") and out[0].raw["confidence_source"] == "self_report"
        assert a.describe().adapter_key == "mlx"
    finally:
        a.close()


@pytest.mark.contract
@pytest.mark.tc("RT-VLLM-001", "RT-MLX-001")
@pytest.mark.parametrize(
    ("cls", "platform"),
    [
        (VllmServerAdapter, "win32"),
        (VllmServerAdapter, "darwin-arm64"),
        (MlxServerAdapter, "linux"),
        (MlxServerAdapter, "win32"),
    ],
)
async def test_unsupported_platform_is_refused(cls, platform):
    a = cls([*FAKE_RUNTIME], "m", platform=platform)
    with pytest.raises(UnsupportedPlatform) as e:
        await a.load(LoadOptions())
    assert e.value.code == "RUNTIME_UNSUPPORTED_PLATFORM" and a.proc is None  # nothing was started
