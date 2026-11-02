"""Pure ranking pieces of hybrid search (05 §2.1–2.2; 09b SRCH-RRF-001/002, SRCH-IMG-003, SRCH-DUP-003)."""

import math
from datetime import datetime

import pytest
from opagent_search.fusion import combine_vectors, fuse, representative, route_weights


@pytest.mark.unit
@pytest.mark.tc("SRCH-RRF-001")
def test_rrf_001_formula_and_weights():
    routes = {"image_vector": ["A", "B"], "fulltext": ["B", "C"], "tag": ["X", "Y", "A"]}
    out = fuse(routes, route_weights('cat'))
    scores = {r.asset_id: r.score for r in out}
    assert math.isclose(scores["A"], 1.0 / 61 + 1.2 / 63)
    assert math.isclose(scores["B"], 1.0 / 62 + 1.0 / 61)
    assert math.isclose(scores["C"], 1.0 / 62)
    assert [r.asset_id for r in out][:3] == ["A", "B", "X"]  # 1.2/61 ≈ 0.01967 ranks above C
    assert next(r for r in out if r.asset_id == "A").matched_by == ["image_vector", "tag"]


@pytest.mark.unit
@pytest.mark.tc("SRCH-RRF-002")
def test_rrf_002_quoted_phrase_boosts_fulltext():
    assert route_weights('"夕陽 海灘"')["fulltext"] == 1.5
    assert route_weights("夕陽 海灘")["fulltext"] == 1.0
    assert route_weights('"a b"')["image_vector"] == 1.0 and route_weights('"a b"')["tag"] == 1.2


@pytest.mark.unit
@pytest.mark.tc("SRCH-RRF-001")
def test_rrf_ties_are_stable():
    out = fuse({"fulltext": ["b", "a"], "tag": ["a", "b"]}, {"fulltext": 1.0, "tag": 1.0})
    assert [r.asset_id for r in out] == ["a", "b"]  # equal scores: by id


@pytest.mark.unit
@pytest.mark.tc("SRCH-IMG-003")
def test_img_003_text_plus_image_vector():
    t, i = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]
    v = combine_vectors(t, i, alpha=0.5)
    assert math.isclose(math.hypot(*v), 1.0)
    assert all(math.isclose(a, b) for a, b in zip(v, [math.sqrt(0.5), math.sqrt(0.5), 0.0], strict=True))
    w = combine_vectors(t, i, alpha=0.8)
    assert w[0] > w[1] > 0
    assert combine_vectors([0, 0], [0, 0], 0.5) == [0.0, 0.0]  # degenerate: no NaN


@pytest.mark.unit
@pytest.mark.tc("SRCH-DUP-003")
def test_dup_003_representative():
    a = {"id": "a", "width": 4000, "height": 3000, "size_bytes": 2_000_000, "taken_at": datetime(2024, 1, 2)}
    b = {"id": "b", "width": 4000, "height": 3000, "size_bytes": 3_000_000, "taken_at": datetime(2024, 1, 3)}
    c = {"id": "c", "width": 6000, "height": 4000, "size_bytes": 1_000_000, "taken_at": datetime(2024, 1, 4)}
    d = {"id": "d", "width": 4000, "height": 3000, "size_bytes": 3_000_000, "taken_at": datetime(2024, 1, 1)}
    assert representative([a, b, c, d])["id"] == "c"  # highest resolution
    assert representative([a, b, d])["id"] == "d"  # then largest file, then earliest
    assert representative([a, {**a, "id": "e", "taken_at": None}])["id"] == "a"  # unknown date sorts last
