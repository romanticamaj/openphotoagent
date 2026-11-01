import pytest

from opagent_core.decide import Stage, VlmResult, decide, route_embedding

EMB_HI = Stage({"food": 0.86, "pets": 0.64, "travel": 0.10, "people": 0.05})  # margin 0.22
EMB_LOWMARGIN = Stage({"food": 0.86, "pets": 0.76, "travel": 0.40, "people": 0.05})  # margin 0.10


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-001")
def test_core_thr_001_high_confidence_embedding_accepted():
    r = route_embedding(EMB_HI, n_examples=3)
    assert (r.route, r.category) == ("accept", "food")
    o = decide(emb=EMB_HI, n_examples=3)
    assert (o.decision, o.source, o.category) == ("accepted", "embedding", "food")


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-002")
def test_core_thr_002_low_margin_goes_to_vlm_with_top3_hints():
    r = route_embedding(EMB_LOWMARGIN, n_examples=3)
    assert r.route == "vlm"
    assert r.hints == ("food", "pets", "travel")


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-003")
def test_core_thr_003_boundaries_inclusive():
    assert route_embedding(Stage({"a": 0.80, "b": 0.65}), n_examples=3).route == "accept"


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-004")
def test_core_thr_004_no_examples_raises_bar():
    assert route_embedding(EMB_HI, n_examples=0).route == "vlm"
    assert route_embedding(Stage({"a": 0.91, "b": 0.5}), n_examples=0).route == "accept"


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-005")
def test_core_thr_005_vlm_accepted():
    o = decide(vlm=VlmResult("food", 0.71))
    assert (o.decision, o.source, o.category) == ("accepted", "vlm", "food")


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-006")
def test_core_thr_006_vlm_other():
    assert decide(vlm=VlmResult("other", 0.95)).decision == "other"


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-007")
def test_core_thr_007_all_below_min_confidence_is_other():
    assert decide(emb=Stage({"food": 0.54, "pets": 0.30})).decision == "other"
    assert decide(vlm=VlmResult("food", 0.40)).decision == "other"


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-008")
def test_core_thr_008_between_review_and_accept_needs_review():
    o = decide(emb=Stage({"food": 0.62, "pets": 0.20}), n_examples=3)
    assert (o.decision, o.category, o.reason) == ("needs_review", "food", "low_confidence")


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-009")
def test_core_thr_009_consensus_boost():
    o = decide(emb=Stage({"food": 0.7, "pets": 0.6}), vlm=VlmResult("food", 0.8), n_examples=3)
    assert o.source == "consensus" and o.category == "food"
    assert o.confidence == pytest.approx(0.94, abs=1e-12)
    assert o.decision == "accepted"


@pytest.mark.unit
@pytest.mark.tc("CORE-THR-010")
def test_core_thr_010_disagreement_takes_vlm_discounted():
    o = decide(emb=Stage({"food": 0.7, "pets": 0.3}), vlm=VlmResult("pets", 0.8), n_examples=3)
    assert o.category == "pets" and o.confidence == pytest.approx(0.64, abs=1e-12)
    assert (o.decision, o.reason) == ("needs_review", "conflict")
