"""SRCH-RRF-009: MCP search_photos and REST /search are one domain service — same photos, same order."""

import pytest
from opagent_testing.mcp_harness import M


@pytest.mark.integration
@pytest.mark.tc("SRCH-RRF-009")
def test_rrf_009_mcp_and_rest_agree(embedded_pg, tmp_path):
    m = M(embedded_pg.dsn, tmp_path)
    for i in range(12):
        m.add(
            f"2025/{i}.jpg",
            caption=("海邊的貓" if i % 2 else "城市夜景"),
            tags=("animal.cat",) if i % 3 else (),
            category="pets" if i % 2 else "travel",
        )
    m.index()
    pat = m.token()
    for q in ("貓", "海邊 貓", "夜景"):
        rest = m.client.post("/api/v1/search", json={"q": q, "limit": 20, "plan": True}).json()
        mcp = m.ok(pat, "search_photos", {"query": q, "limit": 20})
        assert [r["id"] for r in mcp["results"]] == [i["asset_id"] for i in rest["items"]], q
        assert mcp["total_estimate"] == rest["total"]
    rest_f = {"tags_any": ["animal.cat"], "category": "pets", "taken_from": "2025-01-01", "taken_to": "2026-01-01"}
    mcp_f = {"tags_any": ["animal.cat"], "categories": ["pets"], "taken_at": {"from": "2025-01-01", "to": "2026-01-01"}}
    rest = m.client.post("/api/v1/search", json={"q": "貓", "limit": 20, "plan": True, "filters": rest_f}).json()
    mcp = m.ok(pat, "search_photos", {"query": "貓", "limit": 20, "filters": mcp_f})
    assert rest["items"] and [r["id"] for r in mcp["results"]] == [i["asset_id"] for i in rest["items"]]
    m.eng.dispose()
