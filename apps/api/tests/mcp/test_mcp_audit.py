"""MCP-RATE and MCP-AUD (05 §3.7–§3.8)."""

import pytest
import sqlalchemy.exc
from opagent_testing.clock import FakeClock
from opagent_mcp.tools import TOOLS
from opagent_testing.mcp_harness import M


@pytest.fixture
def m(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path, clock=FakeClock())
    x.aid = x.add("2025/a.jpg", caption="貓", tags=("animal.cat",))
    x.index()
    yield x
    x.eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("MCP-RATE-001")
def test_rate_001_151st_read_in_a_minute(m):
    pat = m.token()
    outs = [m.call(pat, "list_tags", {}) for _ in range(151)]
    assert all(not o["result"]["isError"] for o in outs[:150])
    last = outs[150]["result"]
    assert last["isError"] and last["structuredContent"]["code"] == "RATE_LIMITED"
    assert last["structuredContent"]["retry_after_s"] >= 1
    assert m.calls()[-1].status == "rate_limited"


@pytest.mark.integration
@pytest.mark.tc("MCP-RATE-003")
def test_rate_003_mcp_label_jobs_are_bounded(m):
    pat = m.token(["photos:read", "jobs:run"])
    for _ in range(2):
        out = m.ok(pat, "run_labeling_job", {"scope": {"photo_ids": [m.aid]}, "only_unlabeled": False})
        assert out["mode"] == "dry_run"
    third = m.call(pat, "run_labeling_job", {"scope": {"photo_ids": [m.aid]}})["result"]
    assert third["isError"] and third["structuredContent"]["code"] == "MCP_JOB_LIMIT"
    assert "app" in third["structuredContent"]["message"]


@pytest.mark.integration
@pytest.mark.tc("MCP-RATE-003")
def test_rate_003_more_than_50000_items_refused(m, monkeypatch):
    import opagent_api.mcp_tools as t

    monkeypatch.setattr(t, "MCP_JOB_MAX_ITEMS", 0)  # the bound itself, without inserting 50,001 photos
    pat = m.token(["photos:read", "jobs:run"])
    out = m.call(pat, "run_labeling_job", {"scope": {"folders": [{"source": "photos"}]}})["result"]
    assert out["isError"] and out["structuredContent"]["code"] == "SCOPE_TOO_LARGE"
    assert t.MCP_JOB_MAX_ITEMS == 0 and __import__("opagent_mcp.ratelimit").ratelimit.MCP_JOB_MAX_ITEMS == 50_000


@pytest.mark.integration
@pytest.mark.tc("MCP-RATE-004")
def test_rate_004_propose_10_per_hour_and_10000_actions(m, monkeypatch):
    import opagent_api.mcp_tools as t

    pat = m.token(["photos:read", "organize:propose"])
    args = {"scope": {"filters": {}}, "action": "copy", "target": {"source": "sorted"}, "path_template": "{stem}{ext}"}
    outs = [m.call(pat, "propose_organize", args)["result"] for _ in range(11)]
    assert all(not o["isError"] for o in outs[:10])
    assert outs[10]["structuredContent"]["code"] == "RATE_LIMITED"
    m.client.app.state.opa.clock.advance(3600)
    monkeypatch.setattr(t, "PLAN_MAX_ACTIONS", 0)
    big = m.call(pat, "propose_organize", args)["result"]
    assert big["isError"] and big["structuredContent"]["code"] == "SCOPE_TOO_LARGE"


@pytest.mark.integration
@pytest.mark.tc("MCP-RATE-005")
def test_rate_005_face_lookups_limited(m):
    m.settings(faces_enabled=True)
    pat = m.token(["photos:read", "faces:read"])
    codes = [m.call(pat, "find_photos_of_person", {"person_ids": ["p1"]})["result"]["structuredContent"]["code"]
             for _ in range(61)]
    assert codes[:60] == ["NOT_FOUND"] * 60 and codes[60] == "RATE_LIMITED"


@pytest.mark.integration
@pytest.mark.tc("MCP-AUD-001")
def test_aud_001_every_call_is_logged(m):
    m.settings(allow_apply_requests=True, faces_enabled=True)
    pat = m.token(["photos:read"])
    for t in TOOLS:
        m.rpc(pat, "tools/call", {"name": t.name, "arguments": {}, "_meta": {
            "io.modelcontextprotocol/clientInfo": {"name": "Claude Desktop", "version": "1.2.3"}}},
            x_opa_transport="stdio")
    rows = m.calls()
    assert len(rows) == 27
    for r in rows:
        assert r.client_name == "Claude Desktop" and r.client_version == "1.2.3" and r.transport == "stdio"
        assert r.args_redacted == {} and r.args_hash and r.status in ("ok", "error", "denied")
        assert r.latency_ms is not None and r.at is not None and r.token_id == m.last_token["id"]


@pytest.mark.security
@pytest.mark.tc("MCP-AUD-003")
def test_aud_003_call_log_is_append_only(m):
    pat = m.token()
    m.call(pat, "list_tags", {})
    for q in ("update mcp_call_log set status='ok'", "delete from mcp_call_log"):
        with pytest.raises(sqlalchemy.exc.DBAPIError, match="append-only"):
            m.sql(q)
    assert len(m.calls()) == 1


@pytest.mark.integration
@pytest.mark.tc("MCP-AUD-004")
def test_aud_004_side_effects_reach_the_audit_log(m):
    pat = m.token(["photos:read", "labels:write"], name="Cursor")
    out = m.ok(pat, "update_labels", {"id": m.aid, "add_tags": ["海邊"], "note": "user asked"})
    assert out["updated"] == 1 and len(out["review_ids"]) == 1
    a = m.sql("select actor, action from audit_log where action='mcp.update_labels'").one()
    assert a.actor == "mcp:Cursor"
    rv = m.sql("select reason, corrected from review where id=:i", i=out["review_ids"][0]).one()
    assert rv.reason == "mcp:Cursor" and "scene.beach" in rv.corrected["tags"]
    apps = m.client.get("/api/v1/mcp/tokens").json()
    assert apps[-1]["calls"] == 1 and apps[-1]["images_returned"] == 0
