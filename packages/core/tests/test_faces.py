"""Pure face decisions (07 §2.2–§2.6; 09b §4): quality tiers, online assignment, cluster bookkeeping, find tiers."""

import pytest
from opagent_core import faces as F


@pytest.mark.unit
@pytest.mark.tc("FACE-DET-001")
@pytest.mark.parametrize(
    ("min_side", "yaw", "lap", "fiqa", "tier"),
    [
        (28, 10, 100, 0.8, 0),  # too small: bbox only, no embedding
        (40, 10, 100, 0.8, 1),  # small: searchable, never automatic
        (90, 10, 100, 0.8, 3),  # good
        (90, 80, 100, 0.8, 0),  # profile beyond 75°
        (90, 20, 100, 0.8, 3),
        (90, 10, 15, 0.8, 0),  # blurred (Laplacian < 20)
        (60, 10, 100, 0.8, 2),  # ok
        (90, 50, 100, 0.8, 1),  # strong yaw: low
        (90, 10, 100, 0.4, 2),  # fine face, low FIQA: ok, not good
    ],
)
def test_det_001_quality_tiers(min_side, yaw, lap, fiqa, tier):
    assert F.quality_tier(min_side=min_side, yaw=yaw, laplacian=lap, fiqa=fiqa) == tier


@pytest.mark.unit
@pytest.mark.tc("FACE-DET-002")
def test_det_002_big_group_photo_truncated_at_50():
    faces = [{"det_score": 0.7 + i / 1000} for i in range(60)]
    kept, truncated = F.cap_faces(faces)
    assert len(kept) == 50 and truncated is True
    assert min(f["det_score"] for f in kept) >= sorted(f["det_score"] for f in faces)[10]  # the 50 most certain
    assert F.cap_faces(faces[:3]) == (faces[:3], False)


@pytest.mark.unit
@pytest.mark.tc("FACE-DET-003")
def test_det_003_skip_conditions():
    assert F.should_run(category="screenshot") is False
    assert F.should_run(media_kind="document") is False
    assert F.should_run(category="people") is True and F.should_run() is True


def _assign(scores, **kw):
    base = {"neighbours_of_p1": 3, "tier": 2}
    base.update(kw)
    return F.decide_assignment(scores, **base)


@pytest.mark.unit
@pytest.mark.tc("FACE-ASN-001")
def test_asn_001_auto():
    d = _assign({"p1": [0.66, 0.66, 0.66], "p2": [0.5, 0.5, 0.5]}, neighbours_of_p1=2)
    assert (d.kind, d.person, round(d.score, 2), round(d.margin, 2)) == ("auto", "p1", 0.66, 0.16)


@pytest.mark.unit
@pytest.mark.tc("FACE-ASN-002")
def test_asn_002_small_margin_is_a_suggestion():
    assert _assign({"p1": [0.62], "p2": [0.58]}).kind == "suggested"


@pytest.mark.unit
@pytest.mark.tc("FACE-ASN-003")
def test_asn_003_low_quality_never_automatic():
    assert _assign({"p1": [0.9, 0.9, 0.9]}, tier=1).kind == "suggested"
    assert _assign({"p1": [0.9, 0.9, 0.9]}, neighbours_of_p1=1).kind == "suggested"  # min_core = 2


@pytest.mark.unit
@pytest.mark.tc("FACE-ASN-004")
def test_asn_004_age_variation_and_confusable():
    s = {"p1": [0.75, 0.75, 0.75], "p2": [0.64, 0.64, 0.64]}  # margin 0.11: enough normally (0.08)
    assert _assign(s).kind == "auto"
    assert _assign(s, age_variation={"p1"}).kind == "suggested"  # always only suggested
    assert _assign(s, confusable={frozenset({"p1", "p2"})}).kind == "suggested"  # gap < 0.12
    wide = {"p1": [0.8, 0.8, 0.8], "p2": [0.6, 0.6, 0.6]}
    assert _assign(wide, confusable={frozenset({"p1", "p2"})}).kind == "auto"  # gap 0.20 ≥ 0.12
    assert F.margin_for("p1", age_variation={"p1"}) == 0.12 and F.margin_for("p2", age_variation={"p1"}) == 0.08


@pytest.mark.unit
@pytest.mark.tc("FACE-ASN-005")
def test_asn_005_below_suggestion_threshold_is_unassigned():
    d = _assign({"p1": [0.44, 0.44, 0.44]})
    assert d.kind == "unassigned" and d.person is None


@pytest.mark.unit
@pytest.mark.tc("FACE-ASN-006")
def test_asn_006_cannot_link_excludes_the_person():
    d = _assign({"p1": [0.9, 0.9, 0.9], "p2": [0.5, 0.5, 0.5]}, rejected={"p1"})
    assert d.person == "p2" and d.kind == "suggested"


@pytest.mark.unit
@pytest.mark.tc("FACE-ASN-001")
def test_person_score_is_mean_of_top_3():
    assert F.person_score([0.9, 0.1, 0.8, 0.7, 0.2]) == pytest.approx(0.8)


@pytest.mark.unit
@pytest.mark.tc("FACE-CLU-003")
def test_clu_003_ids_stable_by_jaccard():
    old = {"g1": {"a", "b", "c", "d"}, "g2": {"x", "y", "z"}}
    new = [{"a", "b", "c", "e"}, {"x", "q", "r", "s"}, {"m", "n", "o"}]
    ids = F.stable_ids(old, new, new_id=iter(["n1", "n2", "n3"]).__next__)
    assert ids == ["g1", "n1", "n2"]  # 3/5 = 0.6 keeps g1; 1/6 for g2 is too low


@pytest.mark.unit
@pytest.mark.tc("FACE-CLU-004")
def test_clu_004_small_groups_fold_into_others():
    shown, others = F.fold_small({"a": 5, "b": 3, "c": 2, "d": 1})
    assert shown == ["a", "b"] and others == ["c", "d"]


@pytest.mark.unit
@pytest.mark.tc("FACE-MGT-003")
def test_mgt_003_merge_suggestions_only_listed():
    protos = {"A": [[1.0, 0.0]], "B": [[0.6, 0.8]], "C": [[0.0, 1.0]]}
    sug = F.merge_suggestions(protos)
    assert sug == [("A", "B", 0.6), ("B", "C", 0.8)]  # cosine ≥ τ_merge 0.55; nothing is merged here


@pytest.mark.unit
@pytest.mark.tc("FACE-MGT-005")
def test_mgt_005_person_names_are_safe_path_segments():
    for raw in ("媽媽/爸爸", "..", "CON", "a\\b", "x" * 400):
        seg = F.folder_name(raw)
        assert "/" not in seg and "\\" not in seg and seg not in ("..", ".", "CON") and len(seg.encode()) <= 255
    assert F.folder_name("小明") == "小明"


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-001")
def test_find_001_two_tiers_and_asset_max():
    faces = {"f1": ("a1", 0.6), "f2": ("a1", 0.3), "f3": ("a2", 0.5), "f4": ("a3", 0.4)}
    out = F.find_tiers(faces)
    assert out == {"sure": [("a1", 0.6)], "maybe": [("a2", 0.5)]}


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-003")
def test_find_003_only_confirmed_join_the_reference_set():
    assigns = [("f1", "confirmed"), ("f2", "auto"), ("f3", "suggested"), ("f4", "confirmed"), ("f5", "rejected")]
    assert F.reference_faces(assigns) == ["f1", "f4"]


@pytest.mark.unit
@pytest.mark.tc("FACE-CLU-005")
def test_clu_005_chinese_whispers_on_a_knn_graph():
    # two cliques joined by one weak edge, plus a singleton
    edges = {
        ("a", "b"): 0.9,
        ("b", "c"): 0.9,
        ("a", "c"): 0.9,
        ("x", "y"): 0.9,
        ("y", "z"): 0.9,
        ("x", "z"): 0.9,
        ("c", "x"): 0.46,
    }
    groups = F.chinese_whispers(["a", "b", "c", "x", "y", "z", "lonely"], edges, seed=7)
    assert sorted(sorted(g) for g in groups) == [["a", "b", "c"], ["lonely"], ["x", "y", "z"]]
    assert F.chinese_whispers(["a"], {}, seed=1) == [{"a"}]


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-010")
def test_find_010_candidates_for_a_person():
    """Seed → ranked candidates → yes/no (Ente's feedback loop; docs/research/face-search-2026-11.md)."""
    scores = {"near": 0.71, "mine": 0.9, "said_no": 0.8, "same_photo": 0.77, "theirs": 0.75, "weak": 0.3, "soft": 0.6}
    facts = {
        "near": ("a1", None, None),
        "mine": ("a2", "p", "confirmed"),
        "said_no": ("a3", None, None),
        "same_photo": ("a2", None, None),  # the person is already in that photo: someone else
        "theirs": ("a4", "q", "confirmed"),  # someone decided that is another person
        "weak": ("a5", None, None),
        "soft": ("a6", "g", "auto"),  # in an unnamed group: still offered
    }
    out = F.person_candidates("p", scores, facts, rejected={"said_no"})
    assert out == [("near", 0.71), ("soft", 0.6)]


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-014")
def test_find_014_candidates_start_at_one_half():
    """回歸 (audit round 2): from 0.42 about half of the first 20 candidates were someone else. On the reference
    library two faces in one photo (always two people) pass 0.42 about 6% of the time, 0.5 only 1.76%."""
    from opagent_core.faces import CANDIDATE_MIN

    facts = {"a": ("a1", None, None), "b": ("a2", None, None)}
    assert F.person_candidates("p", {"a": 0.52, "b": 0.45}, facts, rejected=set(), low=CANDIDATE_MIN) == [("a", 0.52)]


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-015")
def test_find_015_a_group_nobody_confirmed_asks_for_more_likeness():
    """回歸 (audit round 3): one person's candidates were 3 of 12 right. Before anyone confirms a face the references
    are the group's own faces, wrong ones included, so the bar is higher until a person has said yes."""
    assert F.candidate_floor(confirmed=False) == 0.55
    assert F.candidate_floor(confirmed=True) == F.CANDIDATE_MIN == 0.5
