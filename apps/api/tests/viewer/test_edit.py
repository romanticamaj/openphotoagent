"""Editing v1 (ADR-0057): an edit is a view setting kept in the library and drawn by the page, like a rotation
(ROT-001). The file is never written, and no derived file is made again (invariants 1, 4, 7)."""

from datetime import UTC, datetime

import pytest
from opagent_testing.mcp_harness import M


@pytest.fixture
def m(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path)
    x.a = x.add("2025/a.jpg", caption="a dog", taken=datetime(2025, 3, 1, tzinfo=UTC))  # 4000×3000
    x.sql("update asset set local_at = taken_at where id = :a", a=x.a)
    x.index()
    yield x
    x.eng.dispose()


def put(m, body, aid=None):
    return m.client.put(f"/api/v1/assets/{aid or m.a}/edit", json=body, headers=m.ui)


@pytest.mark.integration
@pytest.mark.tc("EDIT-001")
def test_edit_001_an_edit_is_kept_checked_and_reverted(m):
    edit = {"v": 1, "exposure": 0.5, "contrast": 20, "saturation": -30, "filter": {"name": "warm", "strength": 60}}
    r = put(m, edit)
    assert r.status_code == 200, r.text
    a = m.client.get(f"/api/v1/assets/{m.a}", headers=m.ui).json()
    assert a["edit"] == edit and a["edit_rev"] == 1
    for bad in (
        {"v": 1, "exposure": 3},
        {"v": 1, "contrast": 101},
        {"v": 1, "crop": {"x": 0.5, "y": 0, "w": 0.6, "h": 1}},  # past the right edge
        {"v": 1, "crop": {"x": 0, "y": 0, "w": 0.01, "h": 1}},  # too thin
        {"v": 1, "filter": {"name": "glitter", "strength": 50}},
        {"v": 2},
    ):
        assert put(m, bad).status_code == 422, bad
    assert put(m, {"v": 1}, aid="nope").status_code == 404
    assert m.client.delete(f"/api/v1/assets/{m.a}/edit", headers=m.ui).status_code == 200
    a = m.client.get(f"/api/v1/assets/{m.a}", headers=m.ui).json()
    assert a["edit"] is None and a["edit_rev"] == 2


@pytest.mark.integration
@pytest.mark.tc("EDIT-002")
def test_edit_002_the_timeline_lays_a_crop_out_in_its_new_shape(m):
    put(m, {"v": 1, "crop": {"x": 0, "y": 0, "w": 0.5, "h": 1}})  # the left half of 4000×3000: 2000×3000
    tile = m.client.get("/api/v1/timeline/buckets/2025-03", headers=m.ui).json()["assets"][0]
    assert (tile["w"], tile["h"]) == (2000, 3000)
    assert tile["ed"] == {"v": 1, "crop": {"x": 0, "y": 0, "w": 0.5, "h": 1}}
    m.client.put(f"/api/v1/assets/{m.a}/rotation", json={"degrees": 90}, headers=m.ui)
    tile = m.client.get("/api/v1/timeline/buckets/2025-03", headers=m.ui).json()["assets"][0]
    assert (tile["w"], tile["h"]) == (1500, 4000)  # turned first (3000×4000), then cropped to its left half
    hit = m.client.post("/api/v1/search", json={"q": "dog"}, headers=m.ui).json()["items"][0]
    assert hit["ed"]["crop"]["w"] == 0.5


@pytest.mark.integration
@pytest.mark.tc("EDIT-003")
def test_edit_003_an_edit_touches_no_file_and_asks_no_model(m):
    before = m.sql("select count(*) from worker_task").scalar()
    put(m, {"v": 1, "exposure": 1.0, "crop": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}, "straighten": 5})
    assert m.sql("select count(*) from worker_task").scalar() == before  # nothing for the Worker to do
    q = m.client.post("/api/v1/search", json={"q": "dog"}, headers=m.ui).json()
    assert [h["asset_id"] for h in q["items"]] == [m.a]  # found as before
