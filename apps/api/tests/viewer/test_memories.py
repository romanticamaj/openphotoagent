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


@pytest.mark.integration
@pytest.mark.tc("MEM-026")
def test_mem_026_no_photo_near_today_falls_back_to_the_month(m):
    """回歸 (audit round 135): 那年今日 never showed on 18 November: no earlier photo was within ±3 days, and nothing
    came instead. With none near the day, the same month of earlier years is shown, said so (`scope` month)."""
    r = m.client.get("/api/v1/memories/on-this-day", params={"today": "2026-11-05"}, headers=m.ui)
    body = r.json()
    assert body.get("scope") == "month", body
    assert [g["year"] for g in body["groups"]] == [2025, 2024, 2023]
    assert body["groups"][1]["total"] == 1  # the month's photos, counted
    near = m.client.get("/api/v1/memories/on-this-day", params={"today": "2026-11-12"}, headers=m.ui).json()
    assert near["scope"] == "days"  # the days when there are some (MEM-001)


@pytest.mark.integration
@pytest.mark.tc("MEM-029")
def test_mem_029_a_years_themes_keep_a_place_or_a_thing(embedded_pg, tmp_path):
    """回歸 (audit round 135; ADR-0049 addendum 7): every theme was about people. 10 足球, 10 跳舞, 9 嬰兒, 8 海灘: the
    third slot goes to 海灘, the place, and 嬰兒 is the one left out; in count order."""
    x = M(embedded_pg.dsn, tmp_path)
    day = iter(range(1, 200))
    for tag, n in (("足球", 10), ("跳舞", 10), ("嬰兒", 9), ("海灘", 8)):
        for i in range(n):
            d = next(day)
            x.add(f"{tag}{i}.jpg", tags=(tag,), taken=datetime(2025, 1 + d // 28, 1 + d % 28, 9, tzinfo=UTC))
    for i in range(20):
        x.add(f"o{i}.jpg", taken=datetime(2025, 9, 1 + i, 9, tzinfo=UTC))
    x.index()
    th = x.client.get("/api/v1/memories/themes", headers=x.ui).json()["themes"]
    assert [t["key"] for t in th if t["year"] == 2025] == ["activity.dance", "activity.soccer", "scene.beach"]
    x.eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("MEM-030")
def test_mem_030_the_months_photos_are_picked_across_it(embedded_pg, tmp_path, monkeypatch):
    """回歸 (audit round 136): 那年這個月's 24 photos were the first 24 by time, all from one morning of a month of 424.
    They are picked evenly over the month, first and last included, as a theme's are (MEM-013); the count is all."""
    import opagent_api.routes_viewer as V

    monkeypatch.setattr(V, "MEMORY_MAX", 4)
    x = M(embedded_pg.dsn, tmp_path)
    ids = [x.add(f"m{i}.jpg", taken=datetime(2025, 11, 10 + i, 9, tzinfo=UTC)) for i in range(20)]
    x.index()
    body = x.client.get("/api/v1/memories/on-this-day", params={"today": "2026-11-05"}, headers=x.ui).json()
    x.eng.dispose()
    assert body["scope"] == "month"
    g = body["groups"][0]
    assert g["total"] == 20
    assert [p["asset_id"] for p in g["photos"]] == [ids[0], ids[6], ids[13], ids[19]]


@pytest.mark.integration
@pytest.mark.tc("MEM-033")
def test_mem_033_the_months_first_photos_are_from_different_days(embedded_pg, tmp_path, monkeypatch):
    """回歸 (audit round 137): the month's picks spread over its days, but listed by time the 4 tiles shown were all
    from its first morning. They are listed a day at a time, round the days, so the first ones are different days."""
    import opagent_api.routes_viewer as V

    monkeypatch.setattr(V, "MEMORY_MAX", 8)
    x = M(embedded_pg.dsn, tmp_path)
    for d in range(4):
        for h in range(6):
            x.add(f"d{d}h{h}.jpg", taken=datetime(2025, 11, 10 + d, 8 + h, tzinfo=UTC))
    x.index()
    body = x.client.get("/api/v1/memories/on-this-day", params={"today": "2026-11-05"}, headers=x.ui).json()
    x.eng.dispose()
    days = [p["taken_at"][:10] for p in body["groups"][0]["photos"]]
    assert len(days) == 8
    assert len(set(days[:4])) == 4, days


@pytest.mark.integration
@pytest.mark.tc("MEM-038")
def test_mem_038_themes_leave_out_what_an_event_already_shows(embedded_pg, tmp_path):
    """回歸 (audit round 138; ADR-0049 addendum 8): 「2025 的足球」 was mostly the 足球 outing listed under 活動. The
    themes are computed beside the events, and a theme mostly one of them is left out."""
    x = M(embedded_pg.dsn, tmp_path)
    for i in range(16):
        x.add(f"s{i}.jpg", tags=("足球",), taken=datetime(2025, 5, 3, 1 + i % 10, i, tzinfo=UTC))
    for i in range(4):
        x.add(f"t{i}.jpg", tags=("足球",), taken=datetime(2025, 6, 1 + i, 9, tzinfo=UTC))
    for i in range(10):
        x.add(f"b{i}.jpg", tags=("海灘",), taken=datetime(2025, 7, 1 + i % 3, 9, i, tzinfo=UTC))
    for i in range(30):
        x.add(f"o{i}.jpg", taken=datetime(2025, 9, 1 + i % 28, 9, tzinfo=UTC))
    x.index()
    ev = x.client.get("/api/v1/memories/events", headers=x.ui).json()["events"]
    th = x.client.get("/api/v1/memories/themes", headers=x.ui).json()["themes"]
    x.eng.dispose()
    assert [e["key"] for e in ev] == ["activity.soccer"]  # the outing is listed
    assert [t["key"] for t in th if t["year"] == 2025] == ["scene.beach"]


@pytest.mark.integration
@pytest.mark.tc("MEM-041")
def test_mem_041_hide_and_fewer_are_kept_and_undone(embedded_pg, tmp_path):
    """回歸 (audit round 138; ADR-0049 addendum 8): 隱藏這個回憶 and 少一點「X」, kept in `memory_feedback` (not the
    workspace settings the face switch shares): idempotent, listed, undone by deleting; a bad ref is refused."""
    x = M(embedded_pg.dsn, tmp_path)
    for tag, n in (("足球", 12), ("跳舞", 10)):
        for i in range(n):
            x.add(f"{tag}{i}.jpg", tags=(tag,), taken=datetime(2025, 3, 1 + i, 9, tzinfo=UTC))
    for i in range(20):
        x.add(f"o{i}.jpg", taken=datetime(2025, 9, 1 + i, 9, tzinfo=UTC))
    x.index()
    keys = lambda: [t["key"] for t in x.client.get("/api/v1/memories/themes", headers=x.ui).json()["themes"]]
    assert keys() == ["activity.soccer", "activity.dance"]
    ref = {"kind": "hide", "ref": "theme:2025:activity.soccer"}
    a = x.client.post("/api/v1/memories/feedback", json=ref, headers=x.ui)
    b = x.client.post("/api/v1/memories/feedback", json=ref, headers=x.ui)
    assert a.status_code == b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert keys() == ["activity.dance"]
    listed = x.client.get("/api/v1/memories/feedback", headers=x.ui).json()["items"]
    assert [(i["kind"], i["ref"]) for i in listed] == [("hide", "theme:2025:activity.soccer")]
    assert x.client.delete(f"/api/v1/memories/feedback/{a.json()['id']}", headers=x.ui).status_code == 204
    assert keys() == ["activity.soccer", "activity.dance"]
    x.client.post("/api/v1/memories/feedback", json={"kind": "fewer", "ref": "tag:activity.soccer"}, headers=x.ui)
    assert keys() == ["activity.dance", "activity.soccer"]
    for bad in (
        {"kind": "hide", "ref": "x"},
        {"kind": "fewer", "ref": "event:2025-03-01"},
        {"kind": "pin", "ref": "tag:a.b"},
    ):
        assert x.client.post("/api/v1/memories/feedback", json=bad, headers=x.ui).status_code == 422, bad
    x.eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("MEM-044")
def test_mem_044_a_theme_does_not_reuse_an_events_cover(embedded_pg, tmp_path):
    """回歸 (audit round 139): 「2025 的笑容」 had the same cover as 「2025/11/10 · 玩具」 beside it. A theme's cover is a
    photo no listed event shows first, when it has one."""
    x = M(embedded_pg.dsn, tmp_path)
    ev = [
        x.add(
            f"e{i}.jpg",
            tags=("玩具", "笑容") if i < 8 else ("玩具",),
            taken=datetime(2025, 3, 1, 1 + i % 10, i, tzinfo=UTC),
        )
        for i in range(16)
    ]
    for i in range(9):
        x.add(f"s{i}.jpg", tags=("笑容",), taken=datetime(2025, 6, 1 + i, 9, tzinfo=UTC))
    for i in range(20):
        x.add(f"o{i}.jpg", taken=datetime(2025, 9, 1 + i, 9, tzinfo=UTC))
    x.index()
    evs = x.client.get("/api/v1/memories/events", headers=x.ui).json()["events"]
    th = x.client.get("/api/v1/memories/themes", headers=x.ui).json()["themes"]
    x.eng.dispose()
    covers = {e["photos"][0]["asset_id"] for e in evs}
    assert ev[0] in covers  # the outing's cover is its first photo
    smile = next(t for t in th if t["key"] == "mood.smile")
    assert smile["photos"][0]["asset_id"] not in covers


@pytest.mark.integration
@pytest.mark.tc("MEM-045")
def test_mem_045_hiding_an_outing_does_not_bring_back_its_theme(embedded_pg, tmp_path):
    """回歸 (audit round 140): hiding 「2026/6/22 運動場」 brought 「2026 的運動場」 back as a theme, the same photos
    the person had just hidden. A hidden outing still counts as shown when themes leave out repeats (MEM-038)."""
    x = M(embedded_pg.dsn, tmp_path)
    for i in range(16):
        x.add(f"s{i}.jpg", tags=("足球",), taken=datetime(2025, 5, 3, 1 + i % 10, i, tzinfo=UTC))
    for i in range(4):
        x.add(f"t{i}.jpg", tags=("足球",), taken=datetime(2025, 6, 1 + i, 9, tzinfo=UTC))
    for i in range(10):
        x.add(f"b{i}.jpg", tags=("海灘",), taken=datetime(2025, 7, 1 + i % 3, 9, i, tzinfo=UTC))
    for i in range(30):
        x.add(f"o{i}.jpg", taken=datetime(2025, 9, 1 + i % 28, 9, tzinfo=UTC))
    x.index()
    x.client.post("/api/v1/memories/feedback", json={"kind": "hide", "ref": "event:2025-05-03"}, headers=x.ui)
    ev = x.client.get("/api/v1/memories/events", headers=x.ui).json()["events"]
    th = x.client.get("/api/v1/memories/themes", headers=x.ui).json()["themes"]
    x.eng.dispose()
    assert ev == []
    assert [t["key"] for t in th if t["year"] == 2025] == ["scene.beach"]


@pytest.mark.integration
@pytest.mark.tc("MEM-046")
def test_mem_046_the_hidden_list_names_its_subjects(embedded_pg, tmp_path):
    """The hidden list on 分類 (MEM-048) shows 「2025 的足球」 and 「少一點「笑容」」, not vocabulary ids: each item
    named by a theme or a subject carries the subject's name in the asked language."""
    x = M(embedded_pg.dsn, tmp_path)
    x.index()
    for body in ({"kind": "hide", "ref": "theme:2025:activity.soccer"}, {"kind": "fewer", "ref": "tag:mood.smile"}):
        x.client.post("/api/v1/memories/feedback", json=body, headers=x.ui)
    x.client.post("/api/v1/memories/feedback", json={"kind": "hide", "ref": "event:2026-06-22"}, headers=x.ui)
    items = x.client.get("/api/v1/memories/feedback", params={"lang": "zh-TW"}, headers=x.ui).json()["items"]
    x.eng.dispose()
    names = {i["ref"]: i["name"] for i in items}
    assert names == {"theme:2025:activity.soccer": "足球", "tag:mood.smile": "笑容", "event:2026-06-22": None}


@pytest.mark.integration
@pytest.mark.tc("MEM-051")
def test_mem_051_a_themes_cover_is_its_most_typical_photo(embedded_pg, tmp_path):
    """ADR-0049 addendum 8 (audit rounds 138–139): the cover was the first pick. With image vectors, a theme's cover is
    the photo nearest the mean of its picks; the first one, an outlier, is not."""
    from sqlalchemy import text as sql

    x = M(embedded_pg.dsn, tmp_path)
    ids = [x.add(f"s{i}.jpg", tags=("足球",), taken=datetime(2025, 3, 1 + i, 9, tzinfo=UTC)) for i in range(9)]
    for i in range(20):
        x.add(f"o{i}.jpg", taken=datetime(2025, 9, 1 + i, 9, tzinfo=UTC))
    x.index()
    vec = {ids[0]: "[0,1]"} | {a: "[1,0.1]" for a in ids[1:]} | {ids[5]: "[1,0]"}
    with x.eng.begin() as c:
        sid = c.execute(
            sql(
                "insert into embedding_space(model_key, fingerprint, dim, modality, status)"
                " values ('fake-img', 'fake-img@x', 2, 'image', 'active') returning id"
            )
        ).scalar()
        for a, v in vec.items():
            c.execute(
                sql("insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, CAST(:v AS vector))"),
                {"a": a, "s": sid, "v": v},
            )
    th = x.client.get("/api/v1/memories/themes", headers=x.ui).json()["themes"]
    x.eng.dispose()
    cover = th[0]["photos"][0]["asset_id"]
    assert cover != ids[0]  # the outlier, first by date
    assert cover in ids[1:]


@pytest.mark.integration
@pytest.mark.tc("MEM-054")
def test_mem_054_memory_lists_say_todays_date(embedded_pg, tmp_path):
    """Title templates (ADR-0049 addendum 8): 「今年的足球」, 「去年的足球」 and no year on this year's outings need the
    library's today, from the app's clock (invariant 11), not the page's; the local day (UTC 20:00 is the next day)."""
    from opagent_testing.clock import FakeClock

    x = M(embedded_pg.dsn, tmp_path, clock=FakeClock(datetime(2026, 11, 11, 20, 0, tzinfo=UTC)))
    for path in ("/api/v1/memories/themes", "/api/v1/memories/events"):
        body = x.client.get(path, headers=x.ui).json()
        assert body.get("today") == "2026-11-12", path
    x.eng.dispose()
