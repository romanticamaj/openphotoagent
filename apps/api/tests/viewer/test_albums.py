"""Favourites and albums in the app (ADR-0041): database sets, never files (05 §3.3.5); the originals do not change
(invariant 4). Audit round 26: no ★, no albums."""

import pytest
from opagent_testing.mcp_harness import M


@pytest.fixture
def m(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path)
    x.ids = [x.add(f"2025/p{i}.jpg", caption=f"照片 {i}") for i in range(3)]
    x.index()
    yield x
    x.eng.dispose()


def get(m, path):
    r = m.client.get(f"/api/v1{path}", headers=m.ui)
    assert r.status_code == 200, r.text
    return r.json()


def send(m, method, path, body=None):
    return m.client.request(method, f"/api/v1{path}", json=body, headers=m.ui)


@pytest.mark.integration
@pytest.mark.tc("COL-001")
def test_col_001_a_star_marks_a_favourite_and_takes_it_back(m):
    a = m.ids[0]
    assert get(m, f"/assets/{a}")["favorite"] is False
    assert send(m, "PUT", f"/assets/{a}/favorite").status_code == 200
    assert send(m, "PUT", f"/assets/{a}/favorite").status_code == 200  # twice is once
    assert get(m, f"/assets/{a}")["favorite"] is True
    albums = get(m, "/albums")["albums"]
    assert albums[0]["kind"] == "favorites" and albums[0]["count"] == 1 and albums[0]["cover"]["asset_id"] == a
    assert send(m, "DELETE", f"/assets/{a}/favorite").status_code == 200
    assert get(m, f"/assets/{a}")["favorite"] is False
    assert send(m, "PUT", "/assets/nope/favorite").status_code == 404


@pytest.mark.integration
@pytest.mark.tc("COL-002")
def test_col_002_an_album_holds_photos_newest_added_first(m):
    r = send(m, "POST", "/albums", {"name": "墾丁 2025"})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert send(m, "POST", f"/albums/{aid}/photos", {"asset_ids": [m.ids[0]]}).json()["count"] == 1
    assert send(m, "POST", f"/albums/{aid}/photos", {"asset_ids": [m.ids[1], m.ids[0]]}).json()["count"] == 2
    got = get(m, f"/albums/{aid}")
    assert got["name"] == "墾丁 2025" and got["photos"][0]["asset_id"] == m.ids[1]
    assert get(m, f"/assets/{m.ids[0]}")["albums"] == [{"id": aid, "name": "墾丁 2025"}]
    assert send(m, "DELETE", f"/albums/{aid}/photos/{m.ids[0]}").status_code == 200
    assert [p["asset_id"] for p in get(m, f"/albums/{aid}")["photos"]] == [m.ids[1]]
    assert send(m, "PATCH", f"/albums/{aid}", {"name": "墾丁"}).json()["name"] == "墾丁"


@pytest.mark.integration
@pytest.mark.tc("COL-003")
def test_col_003_an_album_name_is_plain_text_and_needed(m):
    assert send(m, "POST", "/albums", {"name": "  "}).status_code == 422
    r = send(m, "POST", "/albums", {"name": "{date:YYYY}/../x"})
    assert r.status_code == 201
    name = r.json()["name"]
    assert "{" not in name and "/" not in name  # never a path template or a path (INJ-017)
    aid = r.json()["id"]
    assert send(m, "POST", f"/albums/{aid}/photos", {"asset_ids": ["nope"]}).status_code == 404
    assert send(m, "DELETE", "/albums/fav-default").status_code == 409  # 收藏 is not deleted, only emptied


@pytest.mark.integration
@pytest.mark.tc("COL-004")
def test_col_004_deleting_an_album_keeps_its_photos(m):
    aid = send(m, "POST", "/albums", {"name": "暫時"}).json()["id"]
    send(m, "POST", f"/albums/{aid}/photos", {"asset_ids": m.ids})
    assert send(m, "DELETE", f"/albums/{aid}").status_code == 200
    assert all(a["id"] != aid for a in get(m, "/albums")["albums"])
    assert m.sql("select count(*) from asset").scalar() == 3  # the photos stay; only the set is gone


@pytest.mark.integration
@pytest.mark.tc("COL-014")
def test_col_014_a_selection_goes_into_an_album_or_favourites_at_once(m):
    """Audit round 49: search's selection bar had no ★ or 加入相簿. A selection (ids, or 全選's query) is added whole."""
    sel = m.client.post("/api/v1/selections", json={"asset_ids": m.ids[:2]}, headers=m.ui).json()["id"]
    r = send(m, "POST", f"/selections/{sel}/album", {"album_id": "fav-default"})
    assert r.status_code == 200 and r.json()["count"] == 2
    assert get(m, f"/assets/{m.ids[0]}")["favorite"] is True
    aid = send(m, "POST", "/albums", {"name": "選的"}).json()["id"]
    assert send(m, "POST", f"/selections/{sel}/album", {"album_id": aid}).json()["count"] == 2
    assert send(m, "POST", f"/selections/{sel}/album", {"album_id": "nope"}).status_code == 404


@pytest.mark.integration
@pytest.mark.tc("COL-017")
def test_col_017_adding_a_selection_can_be_undone_exactly(m):
    """Audit round 50: the batch toasts had no 復原. The response names the photos it added, not the ones already
    there, and they leave together; undoing never takes out a photo that was in the album before."""
    pre = send(m, "PUT", f"/assets/{m.ids[0]}/favorite")
    assert pre.status_code == 200, pre.text
    sel = m.client.post("/api/v1/selections", json={"asset_ids": m.ids[:3]}, headers=m.ui).json()["id"]
    r = send(m, "POST", f"/selections/{sel}/album", {"album_id": "fav-default"}).json()
    assert sorted(r.get("added", [])) == sorted(m.ids[1:3]) and r["count"] == 3
    back = send(m, "POST", "/albums/fav-default/photos/remove", {"asset_ids": r["added"]})
    assert back.status_code == 200 and back.json()["count"] == 1
    assert get(m, f"/assets/{m.ids[0]}")["favorite"] is True
    assert get(m, f"/assets/{m.ids[1]}")["favorite"] is False
    assert send(m, "POST", "/albums/nope/photos/remove", {"asset_ids": m.ids[:1]}).status_code == 404
