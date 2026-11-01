"""InferenceBackend conformance suite (docs/09 §2.1 contract, 09a §6 INF-CONF-*). Every adapter must pass."""

from __future__ import annotations

import pytest

from opagent_inference.types import BatchRequest, ImageInput, InferenceBackend, LoadOptions, ModelUnavailable

from .backends import FACTORIES

pytestmark = pytest.mark.contract


@pytest.fixture(params=sorted(FACTORIES))
def backend(request: pytest.FixtureRequest) -> InferenceBackend:
    return FACTORIES[request.param]()


def _items(n: int) -> list[ImageInput]:
    return [ImageInput(item_id=f"it{i}", content_hash=f"h{i}", data=bytes([i % 256]) * 16) for i in range(n)]


@pytest.mark.tc("INF-CONF-001")
def test_inf_conf_001_describe_complete(backend):
    info = backend.describe()
    assert info.adapter_key
    assert info.model_fingerprint
    assert info.task_types
    assert info.preferred_input.max_side > 0 and info.preferred_input.format
    assert info.max_batch > 0
    assert info.est_vram_mb >= 0
    assert isinstance(info.supports_structured_output, bool)


@pytest.mark.tc("INF-CONF-002")
async def test_inf_conf_002_load_reentrant_unload_then_unavailable(backend):
    await backend.load(LoadOptions())
    h1 = await backend.health()
    await backend.load(LoadOptions())
    h2 = await backend.health()
    assert h1.ok and h2.ok
    assert h2.vram_used_mb == h1.vram_used_mb  # second load does not allocate again
    await backend.unload()
    with pytest.raises(ModelUnavailable):
        await backend.run_batch(BatchRequest(task_type=backend.describe().task_types[0], items=_items(1)))


@pytest.mark.tc("INF-CONF-003")
@pytest.mark.parametrize("n", [1, 7, 33])
async def test_inf_conf_003_one_output_per_input(backend, n):
    await backend.load(LoadOptions())
    task = backend.describe().task_types[0]
    out = await backend.run_batch(BatchRequest(task_type=task, items=_items(n)))
    assert len(out) == n
    assert {o.item_id for o in out} == {f"it{i}" for i in range(n)}


@pytest.mark.tc("INF-CONF-004")
async def test_inf_conf_004_empty_batch_does_not_load(backend):
    task = backend.describe().task_types[0] if backend.describe().task_types else "classify"
    assert await backend.run_batch(BatchRequest(task_type=task, items=[])) == []
    assert (await backend.health()).vram_used_mb == 0
    assert getattr(backend, "load_count", 0) == 0
