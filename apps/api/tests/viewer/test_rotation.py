"""Rotate (audit round 26 gap): a view setting stored in the library, never written to the file (invariant 4). The
timeline lays a turned photo out with its new shape; the info panel and search carry the angle for display."""

from datetime import UTC, datetime

import pytest
from opagent_testing.mcp_harness import M


@pytest.fixture
def m(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path)
    x.a = x.add("2025/a.jpg", caption="a dog", taken=datetime(2025, 3, 1, tzinfo=UTC))  # 4000×3000
    x.sql("update asset set local_at = taken_at where id = :a", a=x.a)  # the timeline's axis, set by the scanner
    x.index()
    yield x
    x.eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("ROT-001")
def test_rot_001_a_turn_is_kept_and_the_layout_follows_it(m):
    put = lambda deg: m.client.put(f"/api/v1/assets/{m.a}/rotation", json={"degrees": deg}, headers=m.ui)
    assert put(45).status_code == 422
    assert put(90).status_code == 200
    a = m.client.get(f"/api/v1/assets/{m.a}", headers=m.ui).json()
    assert a["rotation"] == 90
    tile = m.client.get("/api/v1/timeline/buckets/2025-03", headers=m.ui).json()["assets"][0]
    assert (tile["w"], tile["h"], tile["rot"]) == (3000, 4000, 90)  # laid out portrait
    hit = m.client.post("/api/v1/search", json={"q": "dog"}, headers=m.ui).json()["items"][0]
    assert hit["rot"] == 90
    assert put(0).status_code == 200
    tile = m.client.get("/api/v1/timeline/buckets/2025-03", headers=m.ui).json()["assets"][0]
    assert (tile["w"], tile["h"], tile["rot"]) == (4000, 3000, 0)
    assert m.client.put("/api/v1/assets/nope/rotation", json={"degrees": 90}, headers=m.ui).status_code == 404
