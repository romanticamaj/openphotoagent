import asyncio
import time

import numpy as np
import pytest
from opagent_core.decide import Policy
from opagent_doctor.probes import load_profile
from opagent_inference.cascade import Cascade, ClassDef, Item, Taxonomy, estimate_distribution
from opagent_inference.fake import FakeBackend
from opagent_inference.types import ImageInput
from opagent_registry.compat import compat
from opagent_registry.loader import load_registry

CLASSES = [ClassDef(c, c.title(), f"photos of {c}") for c in ("people", "food", "pets", "travel", "documents", "other")]


def tax(**kw):
    return Taxonomy(classes=list(kw.pop("classes", CLASSES)), **kw)


def items(n, exif=None):
    return [Item(ImageInput(f"a{i}", f"h{i}", b"x"), exif or {}) for i in range(n)]


def run(c, its, t):
    return asyncio.run(c.run(its, t))


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-001")
def test_inf_cas_001_rule_hit_skips_inference():
    emb, vlm = FakeBackend(scores="low"), FakeBackend(vlm_p=0.9)
    t = tax(rules=[{"if": "exif.is_screenshot == true", "then": "documents"}])
    res = run(Cascade(emb, vlm), items(5, {"is_screenshot": True}), t)
    assert emb.calls() == [] and vlm.calls() == []
    assert len(res) == 5 and all(r.outcome.source == "rule" and r.outcome.category == "documents" for r in res)


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-002")
def test_inf_cas_002_high_confidence_no_vlm():
    emb, vlm = FakeBackend(scores="high"), FakeBackend(vlm_p=0.9)
    res = run(Cascade(emb, vlm), items(100), tax())
    assert vlm.calls("classify") == []
    assert all(r.outcome.source == "embedding" and r.outcome.decision == "accepted" for r in res)


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-003")
def test_inf_cas_003_low_confidence_goes_to_vlm_with_hints():
    emb, vlm = FakeBackend(scores="low"), FakeBackend(vlm_p=0.9)
    run(Cascade(emb, vlm), items(4), tax())
    reqs = vlm.requests("classify")
    assert reqs and sum(len(r.items) for r in reqs) == 4
    for r in reqs:
        cats = r.variables["categories"]
        assert cats[-1] == "other" and 3 <= len(cats) - 1 <= 5
        assert r.variables["hints"] == cats[:-1]


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-004")
def test_inf_cas_004_vlm_still_low():
    emb, vlm = FakeBackend(scores="low"), FakeBackend(vlm_p=0.4)
    res = run(Cascade(emb, vlm, policy=Policy()), items(6), tax())
    assert len(res) == 6 and vlm.calls("classify")
    assert {r.outcome.decision for r in res} <= {"needs_review", "other"}
    assert all(r.error is None for r in res)


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-005")
def test_inf_cas_005_source_and_fingerprint_recorded():
    emb, vlm = FakeBackend(scores="random", seed=3), FakeBackend(vlm_p=0.9, seed=7)
    res = run(Cascade(emb, vlm), items(30), tax(rules=[{"if": "exif.is_screenshot == true", "then": "documents"}]))
    assert len(res) == 30
    for r in res:
        assert r.outcome.source in ("rule", "embedding", "vlm", "consensus")
        assert r.model_fingerprint
        assert r.stages["route"] in ("rule", "accept", "vlm")
    vlm_fp = vlm.describe().model_fingerprint
    assert any(r.model_fingerprint == vlm_fp for r in res if r.stages.get("vlm"))


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-006")
def test_inf_cas_006_no_vlm_fast_mode():
    emb = FakeBackend(scores="low")
    res = run(Cascade(emb, None), items(10), tax())
    assert all(r.outcome.source == "embedding" for r in res)
    assert {r.outcome.decision for r in res} == {"needs_review"}
    assert all(r.error is None for r in res)


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-007")
def test_inf_cas_007_sequential_call_order():
    reg = load_registry()
    c = compat(reg.models["qwen3-vl-8b-instruct"], "q4_k_m", load_profile("rtx4060-laptop"), reg.runtimes)
    assert c.level == "degraded" and [r["code"] for r in c.reasons] == ["VRAM_SEQUENTIAL"]
    emb, vlm = FakeBackend(scores="mixed"), FakeBackend(vlm_p=0.9)
    cas = Cascade(emb, vlm, mode="sequential")
    res = run(cas, items(20), tax())
    kinds = [e[0] + ":" + e[1] for e in cas.events]
    assert kinds[:3] == ["load:embed", "embed:20", "unload:embed"]
    assert kinds[3] == "load:vlm"
    routed = sum(1 for r in res if r.stages["route"] == "vlm")
    assert 0 < routed < 20 and kinds[4] == f"vlm:{routed}"


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-010")
def test_inf_cas_010_estimate_distribution_fast():
    rng = np.random.default_rng(0)
    embs = rng.normal(size=(1000, 16))
    embs /= np.linalg.norm(embs, axis=1, keepdims=True)
    texts = {c.id: np.eye(16)[i] for i, c in enumerate(CLASSES)}
    custom = ClassDef("beach", "Beach", "sea and sand", examples=list(embs[:3]))
    t = tax(classes=[*CLASSES, custom])
    texts["beach"] = np.eye(16)[10]
    t0 = time.perf_counter()
    est = estimate_distribution(embs, t, texts)
    took = time.perf_counter() - t0
    assert took < 0.5
    assert sum(est.counts.values()) == 1000 and est.counts["beach"] >= 3
    assert est.vlm_calls == 0


@pytest.mark.integration
@pytest.mark.tc("Q-025")
def test_q_025_description_change_reuses_image_embeddings():
    emb, vlm = FakeBackend(scores="mixed"), FakeBackend(vlm_p=0.9)
    cas = Cascade(emb, vlm)
    its = items(20)
    run(cas, its, tax())
    img_calls = len(emb.calls("embed"))
    text_calls = emb.text_calls
    vlm_items_before = sum(len(r.items) for r in vlm.requests("classify"))
    changed = [c if c.id != "food" else ClassDef("food", "Food", "meals, desserts and drinks") for c in CLASSES]
    res = run(cas, its, tax(classes=changed))
    assert len(emb.calls("embed")) == img_calls  # image embeddings: cache hits
    assert emb.text_calls == text_calls + 1 and emb.texts_embedded[-1] == ["Food. meals, desserts and drinks"]
    vlm_items_after = sum(len(r.items) for r in vlm.requests("classify")) - vlm_items_before
    affected = [r for r in res if r.stages["route"] == "vlm" and "food" in r.stages["hints"]]
    assert vlm_items_after == len(affected)
