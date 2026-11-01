import json
import math
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opagent_core.decide import (
    Policy,
    Stage,
    VlmResult,
    build_taxonomy,
    decide,
    normalize_logprobs,
    redecide,
    temperature_softmax,
)

GOLDEN = Path(__file__).resolve().parents[4] / "fixtures/golden/decide/CORE-THR-012.json"


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-011")
def test_core_thr_011_rule_first():
    o = decide(emb=Stage({"food": 0.99}), vlm=VlmResult("food", 0.99), rule_category="documents")
    assert (o.decision, o.category, o.confidence, o.source) == ("accepted", "documents", 1.0, "rule")


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-012")
def test_core_thr_012_temperature_softmax_golden():
    g = json.loads(GOLDEN.read_text())
    for case in g:
        got = temperature_softmax(case["sims"], case["T"])
        assert len(got) == len(case["expected"])
        for a, b in zip(got, case["expected"], strict=True):
            assert abs(a - b) <= 1e-9


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-013")
def test_core_thr_013_logprob_normalization_with_floor():
    p = normalize_logprobs({"food": math.log(0.6), "pets": math.log(0.3)}, candidates=["food", "pets", "travel"])
    assert sum(p.values()) == pytest.approx(1.0, abs=1e-12)
    assert p["travel"] == pytest.approx(1e-6 / (0.9 + 1e-6))
    assert p["food"] > p["pets"] > p["travel"]


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-014")
def test_core_thr_014_taxonomy_first_token_collision():
    first_token = {"food": "fo", "foot": "fo", "pets": "pets"}.__getitem__
    tax = build_taxonomy([("food", "美食"), ("foot", "腳"), ("pets", "寵物")], first_token)
    assert [t.id for t in tax] == ["c1_food", "c2_foot", "pets"]
    assert [t.display for t in tax] == ["美食", "腳", "寵物"]
    assert [t.key for t in tax] == ["food", "foot", "pets"]


@pytest.mark.property
@pytest.mark.tc("CORE-THR-015")
@given(p_low=st.floats(0, 1), bump=st.floats(0, 1), second=st.floats(0, 1), n=st.integers(0, 5))
def test_core_thr_015_monotone_in_top1(p_low, bump, second, n):
    p_high = min(1.0, p_low + bump)
    rank = {"accepted": 2, "needs_review": 1, "other": 0}
    lo = decide(emb=Stage({"a": p_low, "b": min(second, p_low)}), n_examples=n)
    hi = decide(emb=Stage({"a": p_high, "b": min(second, p_low)}), n_examples=n)
    assert rank[hi.decision] >= rank[lo.decision]


scores = st.dictionaries(st.text(min_size=1, max_size=4), st.one_of(st.floats(allow_nan=True), st.just(0.0)), max_size=6)


@pytest.mark.property
@pytest.mark.tc("CORE-THR-016")
@given(emb=st.one_of(st.none(), scores), vlm_p=st.one_of(st.none(), st.floats(allow_nan=True)))
def test_core_thr_016_exactly_one_decision_never_lost(emb, vlm_p):
    o = decide(emb=None if emb is None else Stage(emb), vlm=None if vlm_p is None else VlmResult("a", vlm_p))
    assert o.decision in ("accepted", "other", "needs_review")
    assert math.isfinite(o.confidence)
    has_nan = (emb and any(isinstance(v, float) and math.isnan(v) for v in emb.values())) or (
        vlm_p is not None and math.isnan(vlm_p))
    assert ("nan_score" in o.flags) == bool(has_nan)


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-017")
def test_core_thr_017_reapply_thresholds_is_pure_recompute():
    stages = {"emb": {"food": 0.7, "pets": 0.6}, "vlm": {"category": "food", "p": 0.5}, "n_examples": 2}
    strict, loose = Policy(accept=0.95), Policy(accept=0.80)
    fresh = decide(emb=Stage(stages["emb"]), vlm=VlmResult(**stages["vlm"]), n_examples=2, policy=loose)
    assert redecide(stages, loose) == fresh
    assert redecide(stages, strict).decision == "needs_review" and fresh.decision == "accepted"


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-018")
def test_core_thr_018_accept_gates_embedding_fast_path():
    emb = Stage({"food": 0.86, "pets": 0.64})
    assert decide(emb=emb, n_examples=3).decision == "accepted"
    o = decide(emb=emb, n_examples=3, policy=Policy(accept=0.90))
    assert (o.decision, o.reason, o.category) == ("needs_review", "low_confidence", "food")


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-019")
def test_core_thr_019_conflict_below_floor_still_reviewed():
    o = decide(emb=Stage({"food": 0.9, "pets": 0.1}), vlm=VlmResult("pets", 0.6), n_examples=3)
    assert o.confidence == pytest.approx(0.48)
    assert (o.decision, o.reason, o.category) == ("needs_review", "conflict", "pets")
