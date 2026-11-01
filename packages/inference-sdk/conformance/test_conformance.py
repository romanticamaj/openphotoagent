"""InferenceBackend conformance suite (docs/09 §2.1 contract, 09a §6 INF-CONF-*). Every adapter must pass."""

from __future__ import annotations

import pytest
from opagent_inference.types import BatchRequest, ImageInput, LoadOptions, ModelUnavailable

from .backends import FACTORIES

pytestmark = pytest.mark.contract


@pytest.fixture(params=sorted(FACTORIES))
def backend(request: pytest.FixtureRequest):
    from .backends import HARNESSES

    h = HARNESSES.get(request.param)
    yield h.make() if h else FACTORIES[request.param]()
    if h:
        h.cleanup()


def _jpeg(i: int) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (24, 16), ((i * 37) % 256, (i * 91) % 256, (i * 13) % 256)).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _items(n: int) -> list[ImageInput]:
    """Real (tiny) JPEGs: every adapter must be able to decode its inputs."""
    return [ImageInput(item_id=f"it{i}", content_hash=f"h{i}", data=_jpeg(i)) for i in range(n)]


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


# ---- INF-CONF-005..013 (slice 7) ----
from .backends import HARNESSES  # noqa: E402


@pytest.fixture(params=sorted(HARNESSES))
def harness(request: pytest.FixtureRequest):
    h = HARNESSES[request.param]
    yield h
    h.cleanup()


def _task(info) -> str:
    return next(t for t in ("classify", "caption", "tag", "embed") if t in info.task_types)


@pytest.mark.tc("INF-CONF-005")
@pytest.mark.parametrize(
    "fault,code,retryable",
    [
        ("OOM", "OOM", True),
        ("TIMEOUT", "TIMEOUT", True),
        ("BAD_INPUT", "BAD_INPUT", False),
        ("RUNTIME", "RUNTIME", True),
    ],
)
async def test_inf_conf_005_error_mapping(harness, fault, code, retryable):
    from opagent_inference.types import ERROR_CODE_MAP, InferenceError

    b = harness.make()
    await b.load(LoadOptions())
    harness.inject(b, fault)
    with pytest.raises(InferenceError) as e:
        await b.run_batch(BatchRequest(task_type=_task(b.describe()), items=_items(2), deadline_s=5))
    assert e.value.code == code and e.value.retryable is retryable
    assert e.value.error_code == ERROR_CODE_MAP[code]
    assert ERROR_CODE_MAP == {
        "OOM": "MODEL_OOM",
        "TIMEOUT": "RUNTIME_TIMEOUT",
        "BAD_INPUT": "DECODE_FAILED",
        "RUNTIME": "RUNTIME_CRASHED",
    }
    await b.unload()


@pytest.mark.tc("INF-CONF-006")
async def test_inf_conf_006_deadline_cancels_request(harness):
    from opagent_inference.types import InferenceError

    b = harness.make()
    await b.load(LoadOptions())
    harness.slow(b, 2.0)
    with pytest.raises(InferenceError) as e:
        await b.run_batch(BatchRequest(task_type=_task(b.describe()), items=_items(1), deadline_s=0.5))
    assert e.value.code == "TIMEOUT"
    assert harness.was_cancelled(b)
    await b.unload()


@pytest.mark.tc("INF-CONF-007")
async def test_inf_conf_007_embedding_shape(harness):
    b = harness.make()
    info = b.describe()
    if "embed" not in info.task_types:
        assert info.embedding_dim is None
        return
    await b.load(LoadOptions())
    out = await b.run_batch(BatchRequest(task_type="embed", items=_items(3)))
    for o in out:
        assert o.embedding is not None and len(o.embedding) == info.embedding_dim == harness.embedding_dim
        n = sum(x * x for x in o.embedding) ** 0.5
        assert 0.999 <= n <= 1.001
    await b.unload()


@pytest.mark.tc("INF-CONF-008")
async def test_inf_conf_008_deterministic(harness):
    b = harness.make()
    await b.load(LoadOptions())
    task = _task(b.describe())
    req = lambda: BatchRequest(task_type=task, items=_items(4), generation_params={"temperature": 0, "seed": 0})  # noqa: E731
    a1, a2 = await b.run_batch(req()), await b.run_batch(req())
    key = lambda o: (o.item_id, o.category, o.caption, o.tags, o.embedding)  # noqa: E731
    assert sorted(map(key, a1), key=str) == sorted(map(key, a2), key=str)
    await b.unload()


@pytest.mark.tc("INF-CONF-009")
async def test_inf_conf_009_structured_output_valid(harness):
    from opagent_inference.fused import CategoryMap, fused_schema

    b = harness.make()
    info = b.describe()
    if not info.supports_structured_output or "classify" not in info.task_types:
        assert "classify" not in info.task_types
        return
    cats = CategoryMap(keys=["people", "food", "pets", "other"])
    await b.load(LoadOptions())
    out = await b.run_batch(
        BatchRequest(
            task_type="classify",
            items=_items(100),
            output_schema=fused_schema(cats),
            variables={"categories": cats.keys},
        )
    )
    assert len(out) == 100
    for o in out:
        assert o.category in cats.keys and isinstance(o.caption, str) and len(o.caption) <= 400
        assert o.tags is not None and len(o.tags) <= 20
    await b.unload()


@pytest.mark.tc("INF-CONF-010")
async def test_inf_conf_010_same_schema_across_runtimes(harness):
    pair = harness.sibling()
    if pair is None:
        return  # this runtime has no second front-end onto the same server
    outs = []
    for b in (harness.make(), pair):
        await b.load(LoadOptions())
        outs.append(await b.run_batch(BatchRequest(task_type="classify", items=_items(3))))
        await b.unload()
    fields = [{k for k, v in vars(o).items() if v is not None and k not in ("raw", "usage")} for o in outs[0] + outs[1]]
    assert all(f == fields[0] for f in fields)
    assert [o.category for o in outs[0]] == [o.category for o in outs[1]]


@pytest.mark.tc("INF-CONF-011")
async def test_inf_conf_011_adapters_never_receive_paths(harness):
    import ast
    import inspect

    b = harness.make()
    src = inspect.getsource(inspect.getmodule(type(b)))
    calls = [n.func.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "open" not in calls and "Path" not in calls, "adapters must not open files by path (images come as bytes)"
    assert not hasattr(ImageInput(item_id="x", content_hash="h", data=b""), "path")
    await b.load(LoadOptions())
    with pytest.raises((TypeError, ValueError)):
        await b.run_batch(BatchRequest(task_type=_task(b.describe()), items=["/etc/passwd"]))  # type: ignore[list-item]
    await b.unload()


@pytest.mark.tc("INF-CONF-012")
async def test_inf_conf_012_health_reports_vram(harness):
    b = harness.make()
    await b.load(LoadOptions())
    h = await b.health()
    assert h.ok and isinstance(h.vram_used_mb, int) and h.vram_used_mb >= 0
    if not harness.has_gpu:
        assert h.vram_used_mb == 0 or harness.name == "fake"
    await b.unload()


@pytest.mark.tc("INF-CONF-013")
async def test_inf_conf_013_load_progress(harness):
    events: list[tuple[str, object]] = []

    class Rep:
        def start(self, op_type, total=None, unit="items"):
            events.append(("start", op_type))

        def phase(self, name):
            events.append(("phase", name))

        def progress(self, done, total=None):
            events.append(("progress", (done, total)))

        def finish(self, status):
            events.append(("finish", status))

    b = harness.make()
    await b.load(LoadOptions(), reporter=Rep())
    phases = [v for k, v in events if k == "phase"]
    assert phases, "at least one sub-phase is reported"
    fracs = [d / t for k, v in events if k == "progress" for d, t in [v] if t]
    assert all(f <= 0.95 for f in fracs[:-1]) and (not fracs or fracs[-1] == 1.0)
    assert events[-1] == ("finish", "succeeded")
    await b.unload()
