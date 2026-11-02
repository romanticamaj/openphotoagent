"""Offline gazetteer lookups used by the search planner (05 §2.3: 取人口最大歧義解)."""

import pytest
from opagent_geo.gazetteer import Gazetteer


@pytest.mark.unit
@pytest.mark.tc("SRCH-PLAN-006", "SRCH-PLAN-007")
def test_lookup_names_and_countries():
    g = Gazetteer.fixture()
    kyoto = g.lookup("京都")
    assert [p.name for p in kyoto] == ["Kyoto"] and kyoto[0].country_code == "JP"
    kasuga = g.lookup("春日")
    assert [p.admin1 for p in kasuga] == ["Fukuoka", "Hyogo", "Gifu"]  # largest population first
    assert g.lookup("kyoto")[0].name == "Kyoto"  # case-insensitive latin names
    assert g.country("日本") == "JP" and g.country("Japan") == "JP" and g.country("japan") == "JP"
    assert g.country("台灣") == "TW" and g.country("臺灣") == "TW"
    assert g.lookup("不存在的地名") == [] and g.country("火星") is None
