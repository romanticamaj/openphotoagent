"""Rule-based query planner (05 §2.3; 09b SRCH-PLAN). Workspace tz Asia/Taipei, today 2026-11-01."""

from datetime import date
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st
from opagent_geo.gazetteer import Gazetteer
from opagent_search.planner import plan

TODAY = date(2026, 11, 1)
TZ = ZoneInfo("Asia/Taipei")


@pytest.fixture(scope="module")
def gz():
    return Gazetteer.fixture()


def P(q, gz, **kw):
    return plan(q, today=TODAY, tz=TZ, gazetteer=gz, **kw)


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-001")
def test_plan_001_last_year_in_kyoto_cat(gz):
    p = P("去年在京都的貓", gz)
    f = p["filters"]
    assert f["taken_at"] == {"from": "2025-01-01", "to": "2026-01-01"}
    assert f["place"]["city"] == "Kyoto" and f["place"]["admin1"] == "Kyoto" and f["place"]["country_code"] == "JP"
    assert f["tags_any"] == ["animal.cat"]
    assert len(p["planner"]["explain"]) == 3 and p["planner"]["source"] == "rules"


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-002")
def test_plan_002_roc_year(gz):
    assert P("民國 113 年", gz)["filters"]["taken_at"] == {"from": "2024-01-01", "to": "2025-01-01"}
    assert P("民國113年", gz)["filters"]["taken_at"] == {"from": "2024-01-01", "to": "2025-01-01"}


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-003")
def test_plan_003_season(gz):
    assert P("2024 夏天", gz)["filters"]["taken_at"] == {"from": "2024-06-01", "to": "2024-09-01"}


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-004")
def test_plan_004_last_month(gz):
    assert P("上個月", gz)["filters"]["taken_at"] == {"from": "2026-10-01", "to": "2026-11-01"}


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-006")
def test_plan_006_ambiguous_place_takes_largest_and_says_so(gz):
    p = P("春日", gz)
    assert p["filters"]["place"]["city"] == "Kasuga" and p["filters"]["place"]["admin1"] == "Fukuoka"
    assert any("另有" in e and "同名地點" in e for e in p["planner"]["explain"])


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-007")
@pytest.mark.parametrize("q", ["日本", "Japan", "japan"])
def test_plan_007_country(gz, q):
    assert P(q, gz)["filters"]["place"] == {"country_code": "JP"}


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-008")
@pytest.mark.parametrize("q", ["用 Fujifilm 拍的", "富士拍的照片", "fuji"])
def test_plan_008_camera_make(gz, q):
    assert P(q, gz)["filters"]["camera_make"] == "FUJIFILM"


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-009")
def test_plan_009_remaining_words(gz):
    p = P("夕陽 很美 的 照片", gz)
    assert p["text"] == "夕陽 很美"  # 的／照片 are stop words; a tag word stays in text as well (05 §2.3)
    assert "taken_at" not in p["filters"] and "place" not in p["filters"]


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-010")
def test_plan_010_person_alias_needs_faces_read(gz):
    aliases = {"媽媽": "p_12"}
    p = P("媽媽 海邊", gz, persons=aliases, scopes={"photos:read", "faces:read"})
    assert p["filters"]["persons_all"] == ["p_12"] and p["filters"]["tags_any"] == ["scene.beach"]
    q = P("媽媽 海邊", gz, persons=aliases, scopes={"photos:read"})
    assert "persons_all" not in q["filters"] and "媽媽" in q["text"]


@pytest.mark.property
@pytest.mark.tc("SRCH-PLAN-012")
@given(st.text(max_size=60))
def test_plan_012_never_raises_and_ranges_are_ordered(q):
    p = plan(q, today=TODAY, tz=TZ, gazetteer=Gazetteer.fixture())
    assert set(p) >= {"text", "filters", "planner"}
    t = p["filters"].get("taken_at")
    if t:
        assert t["from"] < t["to"]


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-005")
def test_plan_005_lunar_new_year(gz):
    assert P("2025 過年", gz)["filters"]["taken_at"] == {"from": "2025-01-25", "to": "2025-02-03"}


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-013")
def test_plan_013_lunar_new_year_without_a_year_is_the_latest_one(gz):
    assert P("過年", gz)["filters"]["taken_at"] == {"from": "2026-02-13", "to": "2026-02-22"}


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-014")
@pytest.mark.parametrize("q", ["2025 春節", "2025農曆新年", "民國 114 年過年"])
def test_plan_014_synonyms(gz, q):
    assert P(q, gz)["filters"]["taken_at"] == {"from": "2025-01-25", "to": "2025-02-03"}


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-015")
def test_plan_015_new_year_alone_is_not_resolved(gz):
    p = P("新年 煙火", gz)
    assert "taken_at" not in p["filters"] and "新年" in p["text"]
    assert any("元旦" in e and "過年" in e for e in p["planner"]["explain"])


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-016")
def test_plan_016_year_where_eve_is_the_29th(gz):
    assert P("2030 過年", gz)["filters"]["taken_at"] == {"from": "2030-01-30", "to": "2030-02-08"}


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-017")
def test_plan_017_year_outside_the_table(gz):
    p = P("1850 過年", gz)
    assert "taken_at" not in p["filters"] and any("範圍" in e for e in p["planner"]["explain"])


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-019")
@pytest.mark.parametrize("q", ["photo", "photos", "的 照片", "the photos of"])
def test_plan_019_stop_words_only_query_is_kept_as_typed(gz, q):
    """回歸：只有停用詞、沒有任何條件的查詢被清成空字串，搜尋變成「全部照片」（VIS-002 search 頁）。"""
    p = P(q, gz)
    assert p["filters"] == {} and p["text"] == q


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-020")
@pytest.mark.parametrize(
    ("q", "text"),
    [
        ("我有幾張滑雪的照片？", "滑雪"),
        ("我有多少張貓的照片", "貓"),
        ("幫我找海邊的照片", "海邊"),
        ("有沒有狗的照片？", "狗"),
        ("請列出所有衝浪的相片", "衝浪"),
        ("how many photos of cats do I have?", "cats"),
    ],
)
def test_plan_020_a_question_is_not_the_search_text(gz, q, text):
    """回歸: 問 AI asked「我有幾張滑雪的照片？」and counted 0: the whole sentence went to full-text search."""
    assert P(q, gz)["text"] == text


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-023")
def test_plan_023_a_year_with_a_month_or_a_day_leaves_nothing_behind(gz):
    """回歸 (audit round 45): 「2025年11月」 searched for 「年」 and found 0; 「2025年11月10日」 kept 年、11、月、10、日."""
    p = P("2025年11月", gz)
    assert p["text"] == "" and p["filters"]["taken_at"] == {"from": "2025-11-01", "to": "2025-12-01"}
    for q in ("2025年11月10日", "2025/11/10", "2025-11-10", "2025年11月10號"):
        p = P(q, gz)
        assert p["text"] == "", q
        assert p["filters"]["taken_at"] == {"from": "2025-11-10", "to": "2025-11-11"}, q
    # a day with no year is that day in every year, as Google Photos reads it (SRCH-PLAN-026, audit round 47)
    p = P("11月10日的貓", gz)
    assert p["filters"].get("month_day") == "11-10" and "taken_at" not in p["filters"] and "貓" in p["text"]


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-024")
def test_plan_024_this_day_in_an_earlier_year(gz):
    """回歸 (audit round 45): 「去年今天」 and 「去年這個時候」 searched for 今天 / 這個時候 and found 0."""
    p = P("去年今天", gz)
    assert p["text"] == "" and p["filters"]["taken_at"] == {"from": "2025-10-29", "to": "2025-11-05"}
    p = P("去年這個時候拍了哪些照片", gz)
    assert p["filters"]["taken_at"] == {"from": "2025-10-17", "to": "2025-11-17"}
    assert "這個時候" not in p["text"]


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-025")
def test_plan_025_what_was_taken_is_the_question_not_a_word(gz):
    """回歸 (audit round 46): 「去年今天拍了什麼」 searched for 「拍了什麼」 and found 0."""
    for q in ("去年今天拍了什麼", "去年這個時候拍了哪些照片", "2025年11月有哪些照片", "上個月拍了些什麼"):
        p = P(q, gz)
        assert p["text"] == "", (q, p["text"])
        assert p["filters"].get("taken_at"), q


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-027")
def test_plan_027_a_slashed_day_without_a_year(gz):
    """回歸 (audit round 49): 「11/10」 searched for the characters and returned 122 look-alikes."""
    for q in ("11/10", "11/10 的貓", "3/1"):
        p = P(q, gz)
        assert p["filters"].get("month_day") in ("11-10", "03-01"), q
        assert "/" not in p["text"], q
    assert P("2025/11/10", gz)["filters"]["taken_at"] == {"from": "2025-11-10", "to": "2025-11-11"}  # a year: that day


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-028")
def test_plan_028_holidays_are_dates(gz):
    """回歸 (audit round 65): 「去年聖誕節」 found nothing: 聖誕節 was not a date. Christmas Eve to Boxing Day, 中秋 three
    days either side of the lunar 8/15 (as 過年 is around the lunar new year), 母親節 the second Sunday of May; with no
    year, the most recent one that has started (ADR-0008)."""
    assert P("去年聖誕節", gz)["filters"]["taken_at"] == {"from": "2025-12-24", "to": "2025-12-27"}
    assert P("聖誕節", gz)["filters"]["taken_at"] == {"from": "2025-12-24", "to": "2025-12-27"}  # 2026's is still ahead
    assert P("2024 耶誕節", gz)["filters"]["taken_at"] == {"from": "2024-12-24", "to": "2024-12-27"}
    assert P("2025 中秋", gz)["filters"]["taken_at"] == {"from": "2025-10-03", "to": "2025-10-10"}
    assert P("中秋節烤肉", gz)["filters"]["taken_at"] == {
        "from": "2026-09-22",
        "to": "2026-09-29",
    }  # 2026-09-25 has passed
    assert P("母親節", gz)["filters"]["taken_at"] == {"from": "2026-05-10", "to": "2026-05-11"}


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-029")
def test_plan_029_a_range_of_days(gz):
    """回歸 (audit round 66): 「2026年6月16日到6月22日」 used the first day only and left 「到、6、月、22、日」 as words
    no photo showed. A range is both days, the last one included, and none of its parts are words."""
    for q in ("2026年6月16日到6月22日", "2026/6/16~2026/6/22", "2026年6月16日至22日", "2026/6/16 - 6/22"):
        p = P(q, gz)
        assert p["filters"]["taken_at"] == {"from": "2026-06-16", "to": "2026-06-23"}, q
        assert not p["text"].strip(), (q, p["text"])
    assert P("2026年6月22日到6月16日", gz)["filters"]["taken_at"] == {
        "from": "2026-06-16",
        "to": "2026-06-23",
    }  # either way


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-030")
def test_plan_030_more_holidays(gz):
    """回歸 (audit round 66): 兒童節, 清明 and 端午 were not dates. Lunar ones from the generated table (端午 5/5,
    元宵 1/15, 七夕 7/7), fixed ones by the calendar; 跨年 is the last night of the year and the first day after."""
    assert P("2025 端午", gz)["filters"]["taken_at"] == {"from": "2025-05-30", "to": "2025-06-02"}  # 2025-05-31 ± 1
    assert P("2026 元宵", gz)["filters"]["taken_at"] == {"from": "2026-03-02", "to": "2026-03-05"}  # 2026-03-03 ± 1
    assert P("2025 七夕", gz)["filters"]["taken_at"] == {"from": "2025-08-29", "to": "2025-08-30"}
    assert P("2026 兒童節", gz)["filters"]["taken_at"] == {"from": "2026-04-04", "to": "2026-04-05"}
    assert P("2026 清明", gz)["filters"]["taken_at"] == {"from": "2026-04-04", "to": "2026-04-07"}
    assert P("2025 國慶", gz)["filters"]["taken_at"] == {"from": "2025-10-10", "to": "2025-10-11"}
    assert P("2025 跨年", gz)["filters"]["taken_at"] == {"from": "2025-12-31", "to": "2026-01-02"}
    assert P("2026 情人節", gz)["filters"]["taken_at"] == {"from": "2026-02-14", "to": "2026-02-15"}


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-031")
def test_plan_031_a_thing_named_after_a_holiday_is_not_its_date(gz):
    """回歸 (audit round 81): 「聖誕樹」 became the Christmas dates and lost 樹, and found nothing. A tree, a hat or Father
    Christmas is a thing to see, any day of the year; 「聖誕節的聖誕樹」 is still both."""
    for q in ("聖誕樹", "聖誕老人", "聖誕帽"):
        p = P(q, gz)
        assert "taken_at" not in p["filters"], q
        assert p["text"].strip() == q, p["text"]
    assert P("聖誕節的聖誕樹", gz)["filters"]["taken_at"] == {"from": "2025-12-24", "to": "2025-12-27"}
