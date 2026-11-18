import pytest
from opagent_inference.fake import FakeBackend


def _codes(c):
    r = c.post("/api/v1/preflight", json={"action": "search", "params": {}}).json()
    return [b["code"] for b in r["blockers"]], r


def _drain(w):
    for _ in range(50):
        if not w.run_once():
            return


@pytest.mark.integration
def test_search_is_gated_until_the_embedding_model_is_ready(env):
    """04 §8.2 W6: SigLIP2 downloads first so text-to-image search is the first thing that works; until then the
    search bar says why it is unavailable (invariant 8) — downloading with its progress, or not installed."""
    c, w = env["client"], env["worker"]
    codes, r = _codes(c)
    assert codes == ["MODEL_NOT_INSTALLED"] and r["blockers"][0]["params"]["model"] == "embed"
    op = c.post("/api/v1/workers/w1/installs", json={"variant": "siglip2-so400m-384@onnx-fp16"}).json()
    codes, r = _codes(c)
    assert codes == ["MODEL_DOWNLOADING"] and r["blockers"][0]["params"]["model"] == "siglip2-so400m-384@onnx-fp16"
    _drain(w)
    assert c.get(f"/api/v1/operations/{op['operation_id']}").json()["status"] == "succeeded"
    w.embedder = FakeBackend(scores="mixed")  # the worker loads the installed model and reports it
    w.register()
    assert _codes(c)[0] == []
