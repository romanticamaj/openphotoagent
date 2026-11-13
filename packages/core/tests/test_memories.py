"""Theme memories (10 §2.8 主題): a year's frequent subjects, from the tags; pure, no I/O."""

import pytest
from opagent_core.memories import themes


def _rows(year: int, spec: dict[str, int], total: int) -> list[tuple[str, int, list[str]]]:
    """``total`` photos of ``year``; the first ``n`` of them carry tag ``k`` for each (k, n) in spec."""
    rows = []
    for i in range(total):
        tags = [k for k, n in spec.items() if i < n]
        rows.append((f"{year}-{i:04d}", year, tags))
    return rows


@pytest.mark.unit
@pytest.mark.tc("MEM-009")
def test_mem_009_a_years_frequent_subjects_are_its_themes():
    """主題 (10 §2.8, audit round 79: no memories beyond 那年今日). Per year, the vocabulary tags on at least
    ``min_count`` photos, most first, at most ``per_year``; a tag on most of the year's photos (child, outdoor) says
    nothing about it and is left out; a photo list is spread over the theme, never more than ``shown``."""
    rows = _rows(2025, {"people.child": 90, "activity.soccer": 40, "object.flower": 12, "food.cake": 3}, 100)
    rows += _rows(2024, {"activity.drawing": 9}, 20)
    out = themes(rows, min_count=8, per_year=3, common_share=0.6, shown=12)
    assert [(t.year, t.key, t.count) for t in out] == [
        (2025, "activity.soccer", 40),
        (2025, "object.flower", 12),
        (2024, "activity.drawing", 9),
    ]
    assert len(out[0].asset_ids) == 12 and len(set(out[0].asset_ids)) == 12
    assert out[0].asset_ids[0] == "2025-0000" and out[0].asset_ids[-1] == "2025-0039"  # spread end to end
    assert themes([], min_count=8) == []
    # words that are not vocabulary ids (free tags from the VLM) are not themes
    assert themes(_rows(2025, {"球衣": 30}, 40), min_count=8) == []


@pytest.mark.unit
@pytest.mark.tc("MEM-012")
def test_mem_012_a_term_that_says_where_not_what_is_no_theme():
    """回歸 (reference library): 「2025 的室內」「2026 的玩耍」 were themes, on fewer than 60% of their year's photos but
    true of any year. The vocabulary marks such terms; the caller passes them as ``skip``."""
    rows = _rows(2025, {"scene.indoor": 50, "activity.soccer": 40}, 100)
    out = themes(rows, min_count=8, skip={"scene.indoor"})
    assert [t.key for t in out] == ["activity.soccer"]


@pytest.mark.unit
@pytest.mark.tc("MEM-013")
def test_mem_013_a_theme_counts_what_see_all_shows_and_does_not_repeat_another():
    """回歸 (audit round 80): 「2025 的運動場」 shared 28 of its 64 photos with 「2025 的足球」, and every card showed a
    count 「看全部」 did not (119 → 120): duplicates were left out of the count but not out of the search. A theme
    that mostly repeats a higher one is skipped, and the count includes every photo, duplicates too; only the photos
    shown leave the near-duplicates out."""
    rows = _rows(2025, {"activity.soccer": 40}, 100)
    rows = [(a, y, [*t, "scene.sports_field"] if int(a[-4:]) < 30 or int(a[-4:]) >= 90 else t) for a, y, t in rows]
    out = themes(rows, min_count=8, max_overlap=0.5, hidden={"2025-0001"})
    assert [(t.key, t.count) for t in out] == [("activity.soccer", 40)]  # 運動場: 30 of 40 are 足球's
    assert "2025-0001" not in out[0].asset_ids


@pytest.mark.unit
@pytest.mark.tc("MEM-015")
def test_mem_015_a_day_of_many_photos_is_an_event():
    """活動 (10 §2.8, audit rounds 93-98: memories were only 「year + tag」 cards). A day with at least ``min_photos``
    photos is an event, newest first, named by the subject most of its photos share and that is not true of most days;
    each with up to ``shown`` photos spread over the day."""
    from datetime import date

    from opagent_core.memories import events

    rows = [(f"a{i}", date(2025, 11, 13), ["activity.soccer", "people.child"]) for i in range(20)]
    rows += [(f"b{i}", date(2025, 12, 1), ["activity.drawing"]) for i in range(5)]  # too few
    rows += [(f"c{i}", date(2026, 3, 2), ["people.child"]) for i in range(16)]  # no subject: named by the date only
    out = events(rows, min_photos=15, shown=12, skip={"people.child"})
    assert [(e.day.isoformat(), e.key, e.count) for e in out] == [
        ("2026-03-02", None, 16),
        ("2025-11-13", "activity.soccer", 20),
    ]
    assert len(out[1].asset_ids) == 12 and out[1].asset_ids[0] == "a0" and out[1].asset_ids[-1] == "a19"


@pytest.mark.unit
@pytest.mark.tc("MEM-018")
def test_mem_018_one_outing_over_two_days_is_one_event_and_a_mix_is_none():
    """回歸 (audit round 99): 4/8 and 4/9 were two 「手工」 cards, and the newest 「event」 was 80 unrelated photos of
    one import day. Days next to each other with the same subject are one event; a day of many photos with nothing in
    common is not one."""
    from datetime import date

    from opagent_core.memories import events

    rows = [(f"a{i}", date(2026, 4, 8), ["activity.crafts"]) for i in range(16)]
    rows += [(f"b{i}", date(2026, 4, 9), ["activity.crafts"]) for i in range(16)]
    rows += [(f"m{i}", date(2026, 11, 5), [f"object.thing{i}"]) for i in range(20)]  # a mix
    out = events(rows, min_photos=15, named_only=True)
    assert [(e.day.isoformat(), e.last.isoformat(), e.key, e.count) for e in out] == [
        ("2026-04-08", "2026-04-09", "activity.crafts", 32)
    ]
