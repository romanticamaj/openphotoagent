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
from opagent_worker.backends import LLAMA, find_llama_server, load_local, siglip_tokenizer, snapshot_path
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
    install(tmp_path, "auraface-v1", "onnx-fp16")
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
