"""那年今日 (10 §2.8): photos from the same day in earlier years, ±3 days; read-only, nothing on disk changes."""

from datetime import UTC, datetime

import pytest
from opagent_testing.mcp_harness import M


@pytest.fixture
def m(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path)
    x.ids = {
        "y1": x.add("a.jpg", taken=datetime(2025, 11, 10, 9, tzinfo=UTC)),  # 2 days before: in
        "y1b": x.add("b.jpg", taken=datetime(2025, 11, 12, 15, tzinfo=UTC)),
        "y3": x.add("c.jpg", taken=datetime(2023, 11, 15, 8, tzinfo=UTC)),  # 3 days after: in
        "far": x.add("d.jpg", taken=datetime(2024, 11, 20, 8, tzinfo=UTC)),  # 8 days: out
        "now": x.add("e.jpg", taken=datetime(2026, 11, 12, 8, tzinfo=UTC)),  # this year: not a memory
    }
    x.index()
    yield x
    x.eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("MEM-001")
def test_mem_001_on_this_day_groups_earlier_years(m):
    r = m.client.get("/api/v1/memories/on-this-day", params={"today": "2026-11-12"}, headers=m.ui)
    assert r.status_code == 200, r.text
    groups = r.json()["groups"]
    assert [(g["year"], g["years_ago"]) for g in groups] == [(2025, 1), (2023, 3)]
    assert {p["asset_id"] for p in groups[0]["photos"]} == {m.ids["y1"], m.ids["y1b"]}
    assert [p["asset_id"] for p in groups[1]["photos"]] == [m.ids["y3"]]
    assert all(p.get("ch") for g in groups for p in g["photos"])  # thumbnails on desktop
    assert m.ids["far"] not in {p["asset_id"] for g in groups for p in g["photos"]}


@pytest.mark.integration
@pytest.mark.tc("MEM-002")
def test_mem_002_nothing_on_this_day_is_an_empty_list(m):
    r = m.client.get("/api/v1/memories/on-this-day", params={"today": "2026-06-01"}, headers=m.ui)
    assert r.status_code == 200 and r.json()["groups"] == []


@pytest.mark.integration
@pytest.mark.tc("MEM-004")
def test_mem_004_today_is_kept_near_today(m, monkeypatch):
    """Security review of MEM-001: today=9999-12-31 ran a query per year back to the first photo. A date far from the
    server's today is refused, and at most 30 years are looked at."""
    import opagent_api.routes_viewer as V

    for bad in ("9999-12-31", "1800-01-01"):
        r = m.client.get("/api/v1/memories/on-this-day", params={"today": bad}, headers=m.ui)
        assert r.status_code == 422, bad
    asked = []
    real = V._memory_year
    monkeypatch.setattr(V, "_memory_year", lambda c, year, *a: (asked.append(year), real(c, year, *a))[1])
    m.add("old.jpg", taken=datetime(1950, 11, 12, tzinfo=UTC))
    m.index()
    m.client.get("/api/v1/memories/on-this-day", headers=m.ui)
    assert 0 < len(asked) <= V.MEMORY_YEARS
