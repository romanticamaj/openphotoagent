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
