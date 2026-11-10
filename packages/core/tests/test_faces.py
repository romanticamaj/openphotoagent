"""Pure face decisions (07 §2.2–§2.6; 09b §4): quality tiers, online assignment, cluster bookkeeping, find tiers."""

import pytest
from opagent_core import faces as F


@pytest.mark.unit
@pytest.mark.tc("FACE-DET-001")
@pytest.mark.parametrize(
    ("min_side", "yaw", "lap", "fiqa", "tier"),
    [
        (20, 10, 100, 0.8, 0),  # too small: bbox only, no embedding (was 28 px before ADR-0036)
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


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-017")
def test_find_017_a_candidate_must_look_more_like_them_than_anyone_else():
    """回歸 (audit rounds 5-6): one person's candidates were about 3 of 12 right. Held out on the reference library,
    faces of other groups passed the 0.5 floor 664 times against 89 true ones (precision 0.12); asking that a face be
    closer to this person than to any other group kept 88 of 91 true and let 2 others in (0.98, ADR-0034)."""
    facts = {"near": ("a1", None, None), "split": ("a2", "g", "auto"), "nobody": ("a3", None, None)}
    scores = {"near": 0.7, "split": 0.72, "nobody": 0.6}
    margins = {"near": 0.05, "split": -0.02}  # split: closer to its own group g; nobody: no group to compare with
    out = F.person_candidates("p", scores, facts, rejected=set(), margins=margins)
    assert out == [("near", 0.7), ("nobody", 0.6)]


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-019")
def test_find_019_a_face_too_small_or_soft_to_judge():
    """回歸 (audit round 8, 68 judged candidates): 「can't tell」 went with faces under 60 px or soft (Laplacian
    under 40): 13 of 30 such, against 2 of 27 「same」. A turned head (the quality tier's yaw) told nothing: 10 of
    the 「same」 were profiles too (ADR-0035)."""
    assert F.hard_to_judge(min_side=45, blur=110) is True
    assert F.hard_to_judge(min_side=120, blur=27) is True
    assert F.hard_to_judge(min_side=150, blur=160) is False
    assert F.hard_to_judge(min_side=None, blur=None) is False  # nothing measured: not flagged


@pytest.mark.unit
@pytest.mark.tc("FACE-UI-017")
def test_face_ui_017_the_cover_is_the_most_typical_clear_face():
    """回歸 (audit round 8): a group's cover (the sharpest face) looked like someone other than its members, so nobody
    could tell who the group was. The cover is the clear face closest to the group's centroid."""
    vecs = {"odd": [0.0, 1.0], "a": [1.0, 0.05], "b": [0.98, 0.2], "c": [0.99, 0.1], "soft": [1.0, 0.0]}
    clear = {"odd": True, "a": True, "b": True, "c": True, "soft": False}
    assert F.typical_face(vecs, clear) == "b"  # nearest the centroid (odd pulls it up a little); never odd itself
    assert F.typical_face({"x": [1.0, 0.0]}, {"x": False}) == "x"  # nothing clear: still a cover
    assert F.typical_face({}, {}) is None


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-020")
def test_find_020_one_click_yes_only_for_the_sure_ones():
    """回歸 (audit rounds 8-9): 「全部都是」 marked a different child (0.583). Of 68 judged candidates every 「same」 at
    0.6 or more was right; the only 「different」 was under it. Below 0.6 a person says yes one face at a time."""
    assert F.CANDIDATE_SURE == 0.6
    assert F.sure_candidate(0.61) is True and F.sure_candidate(0.583) is False


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-021")
def test_find_021_a_soft_crop_under_50_is_hard_to_judge():
    """回歸 (audit round 9): a blurry tile went unlabelled. At a Laplacian floor of 50, 4 more 「can't tell」 of round 8
    are flagged (48.7, 47.1, 45.4, 47.2) and no more 「same」 (the next is 50.3)."""
    assert F.hard_to_judge(min_side=120, blur=47) is True
    assert F.hard_to_judge(min_side=120, blur=51) is False


@pytest.mark.unit
@pytest.mark.tc("FACE-UI-018")
def test_face_ui_018_the_cover_is_typical_and_sharp():
    """回歸 (audit round 9): the most typical face was often a soft one, and the people wall looked less crisp. Of the
    five most typical clear faces the sharpest is the cover."""
    vecs = {f"f{i}": [1.0, 0.01 * i] for i in range(8)} | {"odd": [0.0, 1.0]}
    clear = dict.fromkeys(vecs, True)
    sharp = {f"f{i}": 60.0 + i for i in range(8)} | {"f3": 900.0, "odd": 5000.0}
    assert F.typical_face(vecs, clear, sharp) == "f3"  # among the most typical; odd is sharpest but not typical


@pytest.mark.unit
@pytest.mark.tc("FACE-DET-012")
@pytest.mark.parametrize(("min_side", "tier"), [(23, 0), (24, 1), (31, 1), (32, 1)])
def test_det_012_faces_from_24_px_are_embedded(min_side, tier):
    """回歸 (audit round 10, ADR-0036): 7 of 11 faces in a team photo were 23-30 px and never findable. Shrunk faces
    on the reference library still land nearest their own person 84% of the time at 24 px (94% at 32)."""
    assert F.quality_tier(min_side=min_side, yaw=10, laplacian=100, fiqa=0.8) == tier
    assert F.small_face(min_side) is (24 <= min_side < 32)


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-022")
def test_find_022_a_small_face_is_offered_only_for_a_named_person_and_closer():
    facts = {"tiny": ("a1", None, None), "ok": ("a2", None, None)}
    scores = {"tiny": 0.53, "ok": 0.53}
    out = F.person_candidates("p", scores, facts, rejected=set(), low=0.5, small={"tiny"}, named=True)
    assert out == [("ok", 0.53)]  # under 0.55 a small face stays out
    scores["tiny"] = 0.57
    assert ("tiny", 0.57) in F.person_candidates(
        "p", scores, facts, rejected=set(), low=0.5, small={"tiny"}, named=True
    )
    assert ("tiny", 0.57) not in F.person_candidates("p", scores, facts, set(), low=0.5, small={"tiny"}, named=False)


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-023")
def test_find_023_a_small_face_is_never_sure_in_find_this_person():
    faces = {"tiny": ("a1", 0.8), "big": ("a2", 0.8)}
    out = F.find_tiers(faces, small={"tiny"})
    assert [a for a, _ in out["sure"]] == ["a2"] and [a for a, _ in out["maybe"]] == ["a1"]


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-024")
def test_find_024_references_are_the_confirmed_faces_then_the_seed():
    """回歸 (audit round 13, ADR-0038): after 6 yes the references fell from the group's 29 faces to those 6 and no new
    candidate came. The group's clustering faces stay references; online auto-assignments never are."""
    rows = [
        ("c1", "confirmed", "find", None),
        ("s1", "auto", "cluster", None),
        ("o1", "auto", "online_knn", None),
        ("c2", "confirmed", "review", None),
        ("s2", "auto", "cluster", None),
    ]
    assert F.candidate_references(rows) == ["c1", "c2", "s1", "s2"]
    many = [(f"c{i}", "confirmed", "find", None) for i in range(30)] + [("s", "auto", "cluster", None)]
    assert F.candidate_references(many) == [f"c{i}" for i in range(24)]  # 24 at most, confirmed first


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-025")
def test_find_025_a_merged_seed_face_stays_a_seed():
    rows = [("m1", "auto", "merge", "cluster"), ("m2", "auto", "merge", "online_knn")]
    assert F.candidate_references(rows) == ["m1"]


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-027")
def test_find_027_a_face_reached_only_through_the_seed_needs_more_likeness():
    facts = {"a": ("a1", None, None), "b": ("a2", None, None)}
    out = F.person_candidates("p", {"a": 0.52, "b": 0.52}, facts, rejected=set(), low=0.5, seed_only={"b"})
    assert out == [("a", 0.52)]


@pytest.mark.unit
@pytest.mark.tc("FACE-FIND-028")
def test_find_028_faint_candidates_come_after_the_likely_ones():
    """回歸 (audit round 14): the first new candidate after a send was a different child at 0.504, shown first. Under
    0.55 a candidate is 「不太像」 and comes after the likelier ones; the hard-to-judge ones stay last."""
    rows = [("faint", 0.51, True), ("blur", 0.7, False), ("good", 0.58, True), ("best", 0.66, True)]
    assert F.candidate_order(rows) == ["best", "good", "faint", "blur"]
    assert F.faint_candidate(0.504) is True and F.faint_candidate(0.55) is False


def _cloud(seed, center, n, spread):
    import random

    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        v = [c + rnd.gauss(0, spread) for c in center]
        s = sum(x * x for x in v) ** 0.5
        out.append([x / s for x in v])
    return out


@pytest.mark.unit
@pytest.mark.tc("FACE-MGT-014")
def test_mgt_014_merge_cards_only_for_groups_as_alike_as_one_group():
    """回歸 (audit round 6): 4 of 5 「可能是同一人？」 cards paired a boy with a girl. Children's centroids sit close
    (0.70-0.83 for different children, 0.76 for halves of one), so the card needs the cross similarity of two groups to
    be about as high as inside each (ADR-0033)."""
    dim = 16
    a_c = [1.0] + [0.0] * (dim - 1)
    b_c = [0.8, 0.6] + [0.0] * (dim - 2)  # centroids 0.8 apart: alike, but two tight groups of their own
    one = _cloud(1, a_c, 12, 0.08)
    groups = {"A1": one[:6], "A2": one[6:], "B": _cloud(2, b_c, 8, 0.08), "tiny": one[:2]}
    got = F.cohesive_merges(groups)
    pairs = {(a, b) for a, b, _ in got}
    assert ("A1", "A2") in pairs  # one person split in two
    assert not {p for p in pairs if "B" in p}  # alike centroids, still two people
    assert not {p for p in pairs if "tiny" in p}  # under three faces: too little to judge
    assert all(r >= F.THRESHOLDS["merge_ratio"] for _, _, r in got)
