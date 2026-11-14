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


@pytest.mark.integration
@pytest.mark.tc("MEM-005")
def test_mem_005_today_is_the_local_day(embedded_pg, tmp_path):
    """回歸 (audit round 45): at 00:20 on 11/12 in Taipei the card used 11/11, the UTC date, and 「今天先不看」 ran out at
    08:00. Today is the local day."""
    from opagent_testing.clock import FakeClock

    x = M(embedded_pg.dsn, tmp_path, clock=FakeClock(datetime(2026, 11, 11, 16, 20, tzinfo=UTC)))
    x.add("a.jpg", taken=datetime(2025, 11, 12, 9, tzinfo=UTC))
    x.index()
    assert x.client.get("/api/v1/memories/on-this-day", headers=x.ui).json()["today"] == "2026-11-12"
    x.eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("MEM-006")
def test_mem_006_a_year_says_how_many_it_has(m, monkeypatch):
    """回歸 (audit rounds 47-48): 「共 24 張」 read as the full count where the window held 424; the cap is a choice."""
    import opagent_api.routes_viewer as V

    monkeypatch.setattr(V, "MEMORY_MAX", 1)
    g = m.client.get("/api/v1/memories/on-this-day", params={"today": "2026-11-12"}, headers=m.ui).json()["groups"][0]
    assert g["year"] == 2025 and len(g["photos"]) == 1 and g["total"] == 2


@pytest.mark.integration
@pytest.mark.tc("MEM-010")
def test_mem_010_themes_name_a_years_frequent_subjects(embedded_pg, tmp_path):
    """主題 (10 §2.8, ADR-0049): 「2025 的足球」, computed when asked from the photos' tags; named, counted, with a
    cover and up to 12 photos each; one per near-duplicate group, as 那年今日 does. Read-only."""
    x = M(embedded_pg.dsn, tmp_path)
    for i in range(10):
        x.add(f"s{i}.jpg", tags=("足球",), taken=datetime(2025, 5, 1 + i, 9, tzinfo=UTC))
    for i in range(3):
        x.add(f"c{i}.jpg", tags=("蛋糕",), taken=datetime(2025, 6, 1 + i, 9, tzinfo=UTC))
    for i in range(12):
        x.add(f"o{i}.jpg", taken=datetime(2025, 7, 1 + i, 9, tzinfo=UTC))
    x.index()
    r = x.client.get("/api/v1/memories/themes", headers=x.ui)
    assert r.status_code == 200, r.text
    th = r.json()["themes"]
    assert [(t["year"], t["key"], t["name"], t["count"]) for t in th] == [(2025, "activity.soccer", "足球", 10)]
    assert len(th[0]["photos"]) == 10 and all(p["ch"] for p in th[0]["photos"])
    x.eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("MEM-014")
def test_mem_014_a_themes_cover_says_its_subject(embedded_pg, tmp_path):
    """回歸 (audit rounds 80-86): 「2026 的運動場」 had an indoor staircase as its cover, the first photo by date that
    carried the tag. The cover is the first one whose caption names the subject too."""
    x = M(embedded_pg.dsn, tmp_path)
    ids = [
        x.add(
            f"s{i}.jpg",
            caption="樓梯間" if i == 0 else ("戶外的運動場" if i == 4 else "一群人"),
            tags=("運動場",),
            taken=datetime(2026, 3, 1 + i, 9, tzinfo=UTC),
        )
        for i in range(9)
    ]
    for i in range(10):  # the year's other photos: a tag on every one of them would say nothing about it
        x.add(f"o{i}.jpg", taken=datetime(2026, 4, 1 + i, 9, tzinfo=UTC))
    x.index()
    th = x.client.get("/api/v1/memories/themes", headers=x.ui).json()["themes"]
    t = next(t for t in th if t["key"] == "scene.sports_field")
    assert t["photos"][0]["asset_id"] == ids[4]
    assert {p["asset_id"] for p in t["photos"]} == set(ids)  # the same photos, the cover first
    x.eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("MEM-016")
def test_mem_016_events_are_days_of_many_photos(embedded_pg, tmp_path):
    """活動 (ADR-0049 addendum 2): a day with many photos, named by what most of them show; newest first; one photo
    per near-duplicate group, so its count is what the day's search shows. Read-only."""
    x = M(embedded_pg.dsn, tmp_path)
    for i in range(16):
        x.add(f"s{i}.jpg", tags=("足球",), taken=datetime(2025, 11, 13, 9, i, tzinfo=UTC))
    for i in range(3):
        x.add(f"o{i}.jpg", taken=datetime(2025, 11, 20, 9, i, tzinfo=UTC))
    x.index()
    r = x.client.get("/api/v1/memories/events", headers=x.ui)
    assert r.status_code == 200, r.text
    ev = r.json()["events"]
    assert [(e["day"], e["key"], e["name"], e["count"]) for e in ev] == [("2025-11-13", "activity.soccer", "足球", 16)]
    assert len(ev[0]["photos"]) == 12 and all(p["ch"] for p in ev[0]["photos"])
    x.eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("MEM-019")
def test_mem_019_events_merge_an_outing_skip_a_mix_and_lead_with_the_subject(embedded_pg, tmp_path):
    """回歸 (audit round 99): two days of one outing were two cards, an import day of unrelated photos was the newest
    「event」, and 2025/11/13 · 運動場 opened on an indoor photo. One outing, one card with its last day; no subject,
    no card; the cover is the first photo whose caption names the subject."""
    x = M(embedded_pg.dsn, tmp_path)
    ids = []
    for d in (13, 14):
        for i in range(16):
            cap = "室內一角" if (d, i) == (13, 0) else ("戶外踢足球" if (d, i) == (13, 4) else "")
            ids.append(
                x.add(f"s{d}-{i}.jpg", caption=cap, tags=("足球",), taken=datetime(2025, 11, d, 9, i, tzinfo=UTC))
            )
    for i in range(16):
        x.add(f"m{i}.jpg", taken=datetime(2026, 1, 5, 9, i, tzinfo=UTC))
    x.index()
    ev = x.client.get("/api/v1/memories/events", headers=x.ui)
    assert ev.status_code == 200, ev.text
    got = ev.json()["events"]
    assert [(e["day"], e["last"], e["name"]) for e in got] == [("2025-11-13", "2025-11-14", "足球")], got
    assert got[0]["photos"][0]["asset_id"] == ids[4]
    x.eng.dispose()
