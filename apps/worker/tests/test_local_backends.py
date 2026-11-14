"""Real adapters from what is on disk (03 §2.4, 08 §3): the path production takes, which `--backend fake` never touched."""

import asyncio
from pathlib import Path

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper
from opagent_adapters.llamacpp import LlamaCppServerAdapter
from opagent_adapters.ort_embed import OrtEmbedAdapter
from opagent_inference.types import BatchRequest, ImageInput, LoadOptions
from opagent_registry.loader import load_registry
from opagent_worker.backends import (
    LLAMA,
    device_args,
    find_llama_server,
    load_local,
    pick_device,
    siglip_tokenizer,
    snapshot_path,
)
from PIL import Image

VLM, VLM_VARIANT = "qwen3-vl-8b-instruct", "q4_k_m"
EMB, EMB_VARIANT = "siglip2-so400m-384", "onnx-fp16"


def install(root: Path, model: str, variant: str, content: bytes = b"x") -> dict[str, Path]:
    """Place a variant's files where the Downloader puts them."""
    m = load_registry().models[model]
    out = {}
    for f in m.pinned_files(variant):
        p = snapshot_path(root, f.repo, m.revision(variant), f.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        out[f.role or f.path] = p
    return out


def tiny_tokenizer(path: Path) -> None:
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace

    t = Tokenizer(WordLevel({"<pad>": 0, "<unk>": 1, "cat": 2, "beach": 3}, unk_token="<unk>"))
    t.pre_tokenizer = Whitespace()
    t.save(str(path))


def tiny_dual_encoder(vision: Path, text: Path, dim: int = 4) -> None:
    """Same outputs as onnx-community's export: last_hidden_state first, the embedding second as `pooler_output`."""

    def model(inp, elem, in_shape, axes) -> onnx.ModelProto:
        nodes = [
            helper.make_node("Cast", [inp], ["f"], to=TensorProto.FLOAT),
            helper.make_node("ReduceMean", ["f"], ["m"], axes=axes, keepdims=0),
            helper.make_node("Unsqueeze", ["m", "one"], ["m2"]),
            helper.make_node("Add", ["ones", "m2"], ["pooler_output"]),
            helper.make_node("Unsqueeze", ["pooler_output", "two"], ["p3"]),
            helper.make_node("Expand", ["p3", "shape"], ["last_hidden_state"]),
        ]
        graph = helper.make_graph(
            nodes,
            "g",
            [helper.make_tensor_value_info(inp, elem, in_shape)],
            [
                helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT, ["b", dim, 3]),
                helper.make_tensor_value_info("pooler_output", TensorProto.FLOAT, ["b", dim]),
            ],
            [
                helper.make_tensor("ones", TensorProto.FLOAT, [dim], [float(i + 1) for i in range(dim)]),
                helper.make_tensor("one", TensorProto.INT64, [1], [1]),
                helper.make_tensor("two", TensorProto.INT64, [1], [2]),
                helper.make_tensor("shape", TensorProto.INT64, [3], [1, 1, 3]),
            ],
        )
        m = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
        m.ir_version = 8
        return m

    onnx.save(model("pixel_values", TensorProto.FLOAT, ["b", 3, "h", "w"], [1, 2, 3]), vision)
    onnx.save(model("input_ids", TensorProto.INT64, ["b", "s"], [1]), text)


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-001")
def test_inf_local_001_nothing_installed_means_no_backends(tmp_path):
    got = load_local(tmp_path, env={})
    assert got.vlm is None and got.embedder is None


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-001")
def test_inf_local_001_installed_vlm_with_a_runtime_becomes_the_llama_adapter(tmp_path):
    files = install(tmp_path, VLM, VLM_VARIANT)
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"")
    got = load_local(tmp_path, env={"OPAGENT_LLAMA_SERVER": str(exe)})
    assert isinstance(got.vlm, LlamaCppServerAdapter)
    argv = got.vlm.args()
    assert argv[0] == str(exe) and str(files["weights"]) in argv and str(files["mmproj"]) in argv
    assert "--jinja" in argv
    fp = got.vlm.describe().model_fingerprint
    assert VLM in fp and VLM_VARIANT in fp  # invariant 7: another quant must not reuse cached results


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-002")
def test_inf_local_002_vlm_without_a_runtime_is_reported_not_faked(tmp_path):
    install(tmp_path, VLM, VLM_VARIANT)
    got = load_local(tmp_path, env={})
    assert got.vlm is None
    assert got.problems == ["RUNTIME_NOT_INSTALLED"]


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-002")
def test_inf_local_002_runtime_lookup_order(tmp_path):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    name = LLAMA
    (bundled / name).write_bytes(b"")
    pack = tmp_path / "packs" / "llamacpp-cuda13" / "1.0.0"
    pack.mkdir(parents=True)
    env = {"OPAGENT_RUNTIME_DIR": str(bundled)}
    assert find_llama_server(env, tmp_path / "packs") == bundled / name
    (pack / name).write_bytes(b"")
    assert find_llama_server(env, tmp_path / "packs") == pack / name  # a GPU pack beats the bundled build
    override = tmp_path / "mine.exe"
    override.write_bytes(b"")
    assert find_llama_server({**env, "OPAGENT_LLAMA_SERVER": str(override)}, tmp_path / "packs") == override


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-003")
def test_inf_local_003_siglip_tokenizer_pads_to_the_fixed_length_and_truncates(tmp_path):
    tiny_tokenizer(tmp_path / "tokenizer.json")
    tok = siglip_tokenizer(tmp_path / "tokenizer.json", length=4)
    ids = tok(["cat beach", "cat cat cat cat cat cat"])
    assert ids.dtype == np.int64 and ids.shape == (2, 4)
    assert ids[0].tolist() == [2, 3, 0, 0]


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-004")
def test_inf_local_004_embedder_reads_pooler_output_not_the_first_output(tmp_path):
    """回歸: `sess.run(None)[0]` is last_hidden_state in the public export, not the embedding."""
    files = install(tmp_path, EMB, EMB_VARIANT)
    tiny_dual_encoder(files["vision"], files["text"])
    tiny_tokenizer(files["tokenizer"])
    got = load_local(tmp_path, env={}, embed_dim=4)
    emb = got.embedder
    assert isinstance(emb, OrtEmbedAdapter)
    img = tmp_path / "a.jpg"
    Image.new("RGB", (32, 32), (200, 10, 10)).save(img)
    item = ImageInput(item_id="a", content_hash="h", data=img.read_bytes())

    async def go():
        await emb.load(LoadOptions())
        out = await emb.run_batch(BatchRequest(task_type="embed", items=[item]))
        return out[0].embedding, await emb.embed_texts(["cat beach"])

    image, text = asyncio.run(go())
    assert len(image) == 4 and text.shape == (1, 4)
    assert np.linalg.norm(image) == pytest.approx(1.0, abs=1e-5)


class _Fp:
    def __init__(self, fp):
        self.fp = fp
        self.closed = False

    def describe(self):
        return type("I", (), {"model_fingerprint": self.fp, "task_types": ("caption",)})()

    def close(self):
        self.closed = True


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-005")
def test_inf_local_005_a_finished_download_goes_live_without_a_restart(tmp_path):
    from types import SimpleNamespace

    from opagent_worker.agent import WorkerAgent

    posted = []

    class Client:
        def post(self, url, json=None):
            posted.append(url)
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})

    a = WorkerAgent(Client(), roots=[], home=tmp_path / "w", worker_id="w1")
    current = {"vlm": None}
    a.backend_factory = lambda: SimpleNamespace(vlm=current["vlm"], embedder=None, problems=[])
    a.refresh_backends()
    assert a.backend is None and posted == []  # nothing changed: no re-registration
    first = _Fp("m@a")
    current["vlm"] = first
    a._cascade = object()
    a.refresh_backends()
    assert a.backend is first and a._cascade is None and posted == ["/worker-api/v1/register"]
    current["vlm"] = _Fp("m@a")  # same model rebuilt: keep the running one
    a.refresh_backends()
    assert a.backend is first and len(posted) == 1
    current["vlm"] = _Fp("m@b")
    a.refresh_backends()
    assert first.closed and a.backend is current["vlm"] and len(posted) == 2


@pytest.mark.unit
@pytest.mark.tc("FACE-ORT-005")
def test_face_ort_005_both_face_models_on_disk_make_the_real_backend(tmp_path):
    from opagent_adapters.ort_face import OrtFaceBackend

    install(tmp_path, "yunet-2023mar", "onnx-fp32")
    assert load_local(tmp_path, env={}).faces is None  # a detector alone cannot embed
    install(tmp_path, "auraface-v1", "onnx-fp32")
    got = load_local(tmp_path, env={}).faces
    assert isinstance(got, OrtFaceBackend)
    assert "yunet" in got.fingerprint and "auraface" in got.fingerprint  # faces re-run when either model changes


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-004")
def test_inf_local_004_embedding_size_comes_from_the_catalogue(tmp_path):
    files = install(tmp_path, EMB, EMB_VARIANT)
    tiny_tokenizer(files["tokenizer"])
    assert load_local(tmp_path, env={}).embedder.describe().embedding_dim == 1152


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-005")
def test_inf_local_005_a_bad_model_on_disk_does_not_take_the_worker_down(tmp_path, caplog):
    """回歸: an exception here killed the worker at start, the shell restarted it into the same crash, and the download
    that was running stopped for good."""
    from types import SimpleNamespace

    from opagent_worker.agent import WorkerAgent

    class Client:
        def post(self, url, json=None):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})

    a = WorkerAgent(Client(), roots=[], home=tmp_path / "w", worker_id="w1")

    def boom():
        raise ValueError("corrupt tokenizer")

    a.backend_factory = boom
    a.refresh_backends()
    assert a.backend is None
    assert "corrupt tokenizer" in caplog.text


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-006")
@pytest.mark.parametrize(
    ("listing", "device"),
    [
        (
            "Available devices:\n  Vulkan0: NVIDIA GeForce RTX 5070 Ti (15995 MiB, 15227 MiB free)\n"
            "  Vulkan1: AMD Radeon(TM) Graphics (33613 MiB, 31933 MiB free)\n",
            "Vulkan0",
        ),
        (
            "  Vulkan0: Intel(R) UHD Graphics 770 (16000 MiB, 1 MiB free)\n  Vulkan1: AMD Radeon RX 7800 XT (16368 MiB, 1 MiB free)\n",
            "Vulkan1",
        ),
        (
            "  CUDA0: NVIDIA GeForce RTX 4090 (24000 MiB, 1 MiB free)\n  CUDA1: NVIDIA GeForce RTX 3060 (12000 MiB, 1 MiB free)\n",
            "CUDA0",
        ),
        ("  Vulkan0: Intel(R) Iris(R) Xe Graphics (8000 MiB, 1 MiB free)\n", "Vulkan0"),  # only an iGPU: still use it
        ("Available devices:\n  (none)\n", None),
    ],
)
def test_inf_local_006_the_discrete_gpu_is_chosen(listing, device):
    """回歸: the model was split over the 5070 Ti and the integrated Radeon: 27 tok/s, against 85 on the GPU alone."""
    assert pick_device(listing) == device


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-006")
def test_inf_local_006_cpu_can_be_forced(tmp_path):
    assert device_args(tmp_path / "x", {"OPAGENT_LLAMA_CPU": "1"}) == ["--device", "none"]


class _Backlog:
    """The two calls of the backlog, with the worker's real file handling behind them."""

    def __init__(self, items):
        self.items, self.posted, self.asked = items, [], []

    def get(self, url, params=None):
        from types import SimpleNamespace

        self.asked.append(params)
        after = (params or {}).get("after") or ""
        rest = [i for i in self.items if i["asset_id"] > after][: params["limit"]]
        body = {"items": rest, "next": rest[-1]["asset_id"] if rest else after}
        return SimpleNamespace(status_code=200, json=lambda: body, raise_for_status=lambda: None)

    def post(self, url, json=None):
        from types import SimpleNamespace

        self.posted.append((url, json))
        return SimpleNamespace(status_code=200, json=lambda: {}, raise_for_status=lambda: None)


@pytest.mark.unit
@pytest.mark.tc("FACE-BKL-002")
def test_face_bkl_002_idle_worker_scans_the_backlog_once_and_survives_a_bad_file(tmp_path, sandbox_tree):
    from opagent_inference.fake_faces import FakeFaceBackend
    from opagent_worker.agent import WorkerAgent

    Image.new("RGB", (64, 64), (9, 9, 9)).save(sandbox_tree.photos / "IMG_p1.jpg")
    items = [
        {"asset_id": "a1", "root_alias": "photos", "rel": "IMG_p1.jpg"},
        {"asset_id": "a2", "root_alias": "photos", "rel": "gone.jpg"},
    ]
    client = _Backlog(items)
    a = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w", worker_id="w1")
    assert a.faces_backlog_once() == 0  # no face models yet: nothing to do
    a.face_backend = FakeFaceBackend()
    assert a.faces_backlog_once() == 2
    assert [u for u, _ in client.posted] == ["/worker-api/v1/faces"]  # the missing file is skipped, not fatal
    body = client.posted[0][1]
    assert body["asset_id"] == "a1" and len(body["faces"]) == 1 and "person_hint" not in body["faces"][0]
    assert a.faces_backlog_once() == 0  # the cursor moved on
    fresh = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w", worker_id="w1")
    fresh.face_backend = a.face_backend
    assert fresh.faces_backlog_once() == 0  # and it survives a restart
    a.face_backend = FakeFaceBackend()
    a.face_backend.fingerprint = "another-model"
    assert a.faces_backlog_once() == 2  # a new model starts over


class _LazyEmbedder:
    """Like OrtEmbedAdapter: nothing works until load()."""

    def __init__(self):
        self.loaded = False
        self.loads = 0

    def describe(self):
        return type("I", (), {"model_fingerprint": "emb", "task_types": ("embed",)})()

    async def load(self, opts=None, reporter=None):
        self.loaded = True
        self.loads += 1

    async def embed_texts(self, texts):
        if not self.loaded:
            raise RuntimeError("text encoder not loaded")
        return [[1.0, 0.0] for _ in texts]


def _agent(tmp_path):
    from types import SimpleNamespace

    from opagent_worker.agent import WorkerAgent

    class Client:
        def post(self, url, json=None):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {}, status_code=204)

        def get(self, url, params=None):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"items": [], "next": ""})

    return WorkerAgent(Client(), roots=[], home=tmp_path / "w", worker_id="w1", embedder=_LazyEmbedder())


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-007")
def test_inf_local_007_search_works_right_after_a_worker_start(tmp_path):
    """回歸: only a job ever loaded the embedder, so every worker start broke text search until something was organized."""
    a = _agent(tmp_path)
    assert a._do_embed_text({"text": "熊", "modality": "image"}) == {"vec": [1.0, 0.0]}


@pytest.mark.unit
@pytest.mark.tc("INF-LOCAL-007")
def test_inf_local_007_an_idle_worker_keeps_the_embedder_warm(tmp_path):
    """A cold load takes 5-10 s and the search RPC waits 2 s: the first search would always fall back to text."""
    a = _agent(tmp_path)
    a.warm_embedder()
    assert a.embedder.loaded and a.embedder.loads == 1
    a.warm_embedder()
    assert a.embedder.loads == 1


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-021")
def test_srch_plan_021_a_worker_with_no_planner_says_so(tmp_path):
    """回歸: every search logged UNKNOWN_RPC: the API asks for a plan, and no worker ever had the handler."""
    from opagent_worker.agent import RPC_KINDS

    a = _agent(tmp_path)
    assert "query_plan" in RPC_KINDS
    assert a._do_query_plan({"q": "貓"}) == {}  # the API falls back to the rule planner (SRCH-PLAN-011)


class _Due:
    def __init__(self):
        self.calls = []

    def post(self, path, json=None, **kw):
        self.calls.append((path, json))

        class R:
            status_code = 200

            def json(_s):
                return {"ran": False}

        return R()


@pytest.mark.unit
@pytest.mark.tc("FACE-CLU-010")
def test_face_clu_010_the_idle_worker_asks_for_grouping(tmp_path, sandbox_tree):
    from opagent_inference.fake_faces import FakeFaceBackend
    from opagent_worker.agent import WorkerAgent

    client = _Due()
    a = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w", worker_id="w1")
    a.faces_cluster_once(0)
    assert client.calls == []  # no face models: nothing to group
    a.face_backend = FakeFaceBackend()
    a.faces_cluster_once(32)  # still working through the backlog: only a full batch is worth a run
    a.faces_cluster_once(0)  # finished: whatever is waiting
    a.faces_cluster_once(0)  # and not again on every idle tick
    assert [j["min_new"] for _, j in client.calls] == [200, 1]


@pytest.mark.unit
@pytest.mark.tc("INF-VRAM-005")
def test_inf_vram_005_the_worker_lets_go_of_an_idle_vlm(tmp_path, sandbox_tree):
    """回歸 (reference machine): hours after analysis ended, llama-server still held 17.6 GB of RAM and most of the
    GPU; ResourceManager's idle unload (03 §4, INF-VRAM-004) was never wired into the worker."""
    from opagent_inference.fake import FakeBackend
    from opagent_testing.clock import FakeClock
    from opagent_worker.agent import VLM_IDLE_UNLOAD_S, WorkerAgent

    clk = FakeClock()
    vlm = FakeBackend()
    a = WorkerAgent(_Due(), roots=sandbox_tree.roots, home=tmp_path / "w", worker_id="w1", backend=vlm, clock=clk)
    asyncio.run(vlm.load(LoadOptions()))
    a.vlm_used()
    clk.advance(VLM_IDLE_UNLOAD_S - 1)
    assert a.unload_idle_vlm() is False and vlm.loaded
    clk.advance(1)
    assert a.unload_idle_vlm() is True and not vlm.loaded
    assert a.unload_idle_vlm() is False  # once
    assert VLM_IDLE_UNLOAD_S == 600  # 03 §4: ten minutes


@pytest.mark.unit
@pytest.mark.tc("SRCH-R2-006")
def test_srch_r2_006_caption_vectors_run_on_the_bundled_llama_cpp(tmp_path, sandbox_tree):
    """Caption vectors were only ever a fake in tests: no model, no adapter, so 「建立索引」 could not run (ADR-0014).
    Qwen3-Embedding runs in the llama.cpp the app ships; queries carry its instruction, captions go in as they are."""
    from opagent_adapters.llamacpp_embed import LlamaCppEmbedAdapter
    from opagent_worker.agent import WorkerAgent

    a = LlamaCppEmbedAdapter(["llama-server"], "q.gguf", query_prefix="Instruct: find\nQuery: ", fingerprint="qe")
    args = a.args()
    assert "--embedding" in args and args[args.index("--pooling") + 1] == "last" and "--mmproj" not in args
    sent = []

    async def fake(texts):
        sent.append(list(texts))
        return np.ones((len(texts), 4), dtype=np.float32) / 2

    a._embeddings = fake
    a.loaded = True
    assert asyncio.run(a.embed_texts(["一隻貓"])).shape == (1, 4)
    asyncio.run(a.embed_queries(["貓"]))
    assert sent == [["一隻貓"], ["Instruct: find\nQuery: 貓"]]
    w = WorkerAgent(_Due(), roots=sandbox_tree.roots, home=tmp_path / "w", worker_id="w1")
    w.text_embedders = {"qwen3-embedding-0.6b": a}
    out = w._do_embed_text({"text": "海邊", "modality": "caption", "model": "qwen3-embedding-0.6b"})
    assert "vec" in out and sent[-1] == ["Instruct: find\nQuery: 海邊"]  # a search is a query


@pytest.mark.unit
@pytest.mark.tc("SRCH-R2-006")
def test_srch_r2_006_an_installed_text_model_becomes_a_caption_embedder(tmp_path):
    from opagent_adapters.llamacpp_embed import LlamaCppEmbedAdapter

    files = install(tmp_path, "qwen3-embedding-0.6b", "q8_0")
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"")
    got = load_local(tmp_path, env={"OPAGENT_LLAMA_SERVER": str(exe)})
    emb = got.text_embedders["qwen3-embedding-0.6b"]
    assert isinstance(emb, LlamaCppEmbedAdapter) and str(files["weights"]) in emb.args()
    assert emb.query_prefix.startswith("Instruct:") and got.vlm is None  # not mistaken for the VLM


@pytest.mark.unit
@pytest.mark.tc("SRCH-R2-006")
def test_srch_r2_006_building_the_index_takes_a_numpy_answer(tmp_path, sandbox_tree):
    """回歸 (reference machine): every embed_captions task failed with "truth value of an array is ambiguous": a real
    model answers with a numpy array, the fake in the tests with a list."""
    from opagent_adapters.llamacpp_embed import LlamaCppEmbedAdapter
    from opagent_worker.agent import WorkerAgent

    a = LlamaCppEmbedAdapter(["llama-server"], "q.gguf", fingerprint="qe")

    async def fake(texts):
        return np.ones((len(texts), 4), dtype=np.float32) / 2

    a._embeddings = fake
    a.loaded = True
    w = WorkerAgent(_Due(), roots=sandbox_tree.roots, home=tmp_path / "w", worker_id="w1")
    w.text_embedders = {"qwen3-embedding-0.6b": a}
    out = w._do_embed_captions({"model": "qwen3-embedding-0.6b", "items": [{"asset_id": "a1", "text": "一隻貓"}]})
    assert out["dim"] == 4 and out["vectors"]["a1"] == [0.5, 0.5, 0.5, 0.5]


class _Reembed:
    def __init__(self, items):
        self.items, self.posted = items, []

    def get(self, url, params=None):
        from types import SimpleNamespace

        body = {"items": self.items}
        return SimpleNamespace(status_code=200, json=lambda: body, raise_for_status=lambda: None)

    def post(self, url, json=None):
        from types import SimpleNamespace

        self.posted.append((url, json))
        return SimpleNamespace(status_code=200, json=lambda: {}, raise_for_status=lambda: None)


@pytest.mark.unit
@pytest.mark.tc("FACE-DET-013")
def test_det_013_the_worker_embeds_stored_small_faces_again(tmp_path, sandbox_tree):
    """ADR-0036: detect again, match each stored box by overlap, send its vector; none when it is not there."""
    from opagent_inference.fake_faces import FakeFaceBackend
    from opagent_worker.agent import WorkerAgent

    Image.new("RGB", (64, 64), (9, 9, 9)).save(sandbox_tree.photos / "IMG_p1.jpg")
    fb = FakeFaceBackend()
    here = fb.detect("IMG_p1.jpg")[0]["bbox"]
    items = [
        {
            "asset_id": "a1",
            "root_alias": "photos",
            "rel": "IMG_p1.jpg",
            "faces": [{"face_id": "f1", "bbox": here}, {"face_id": "f2", "bbox": [0.0, 0.0, 0.05, 0.05]}],
        },
        {"asset_id": "a2", "root_alias": "photos", "rel": "gone.jpg", "faces": [{"face_id": "f3", "bbox": here}]},
    ]
    client = _Reembed(items)
    a = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w", worker_id="w1")
    assert a.faces_reembed_once() == 0  # no face models: nothing to do
    a.face_backend = fb
    assert a.faces_reembed_once() == 2
    sent = {u.split("/")[-2]: body for u, body in client.posted}
    assert sent["f1"]["vec"] and sent["f1"]["quality_tier"] >= 1
    assert sent["f2"] == {"vec": None} and sent["f3"] == {"vec": None}  # not found / file gone: tried once
