"""RT-TORCH-001/002 (09b §7.1): the torch sidecar for transformers-only models (JoyCaption, Florence-2).

The INF-CONF suite runs it through ``--fake-pipeline`` (no torch installed here); real weights are nightly GPU."""

import pytest
from opagent_adapters.torch_server import arch_check


@pytest.mark.unit
@pytest.mark.tc("RT-TORCH-002")
@pytest.mark.parametrize(
    ("cc", "arch_list", "result"),
    [
        ("12.0", ["sm_80", "sm_86", "sm_90"], "cuda_arch"),  # Blackwell without sm_120: not usable
        ("12.0", ["sm_80", "sm_90", "sm_100", "sm_120"], None),
        ("8.9", ["sm_80", "sm_86", "sm_90"], None),  # PTX/binary for an older arch in the same major family
        ("8.6", ["sm_80", "sm_86"], None),
        ("7.0", ["sm_80", "sm_86"], "cuda_arch"),  # older than anything built
        (None, [], None),  # no GPU: CPU/MPS path
    ],
)
def test_rt_torch_002_blackwell_self_check(cc, arch_list, result):
    assert arch_check(cc, arch_list) == result


@pytest.mark.unit
@pytest.mark.tc("RT-TORCH-002")
def test_rt_torch_002_pack_failure_for_a_pack_without_sm120():
    from opagent_doctor.probes import load_profile
    from opagent_registry.compat import pack_failure
    from opagent_registry.loader import BUILTIN_DIR, load_registry

    reg = load_registry(BUILTIN_DIR)
    hw = load_profile("rtx5070ti-win11")  # Blackwell
    ok = reg.runtimes["torch-cu128"]
    assert pack_failure(ok, hw, reg.gpu_classes) is None
    old = ok.model_copy(
        update={
            "id": "torch-cu124",
            "requires": ok.requires.model_copy(update={"cuda_architectures": ["80", "86", "89", "90"]}),
        }
    )
    assert pack_failure(old, hw, reg.gpu_classes) == "GPU_ARCH_UNSUPPORTED"


@pytest.mark.contract
@pytest.mark.tc("RT-TORCH-001")
async def test_rt_torch_001_sidecar_answers_and_takes_only_in_memory_images():
    import httpx
    from opagent_adapters.managed import TorchServerAdapter
    from opagent_adapters.testing import TORCH_SIDECAR
    from opagent_inference.types import BatchRequest, ImageInput, LoadOptions

    a = TorchServerAdapter(TORCH_SIDECAR, "florence-2-base", categories=["cat", "dog"], platform="linux")
    assert "--trust-remote-code" not in a.args()
    try:
        await a.load(LoadOptions())
        import io

        from PIL import Image

        b = io.BytesIO()
        Image.new("RGB", (8, 8)).save(b, "JPEG")
        out = await a.run_batch(
            BatchRequest(task_type="caption", items=[ImageInput(item_id="x", content_hash="h", data=b.getvalue())])
        )
        assert out[0].caption and a.describe().adapter_key == "torch"
        r = httpx.post(
            a.base_url + "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "file:///etc/passwd"}}]}
                ]
            },
        )
        assert r.status_code == 400  # paths are never read
    finally:
        a.close()
    assert "--trust-remote-code" in TorchServerAdapter(TORCH_SIDECAR, "m", trust_remote_code=True).args()
