"""MCP-APPR (05 §3.6; invariant 5): an agent can only ask; a person approves in the app."""

from datetime import timedelta

import pytest
from opagent_testing.clock import FakeClock
from opagent_testing.mcp_harness import M

TPL = "{taken_at:%Y}/{stem}{ext}"


def _setup(x):
    x.ids = [x.add(f"2025/trip/{i}.jpg", caption="海邊", tags=("scene.beach",)) for i in range(3)]
    x.index()
    x.settings(allow_apply_requests=True)
    pat = x.token(["photos:read", "organize:propose"])
    x.sql("update mcp_token set scopes = array_append(scopes, 'organize:apply') where id = :i", i=x.last_token["id"])
    x.tid = x.last_token["id"]
    return pat


@pytest.fixture
def m(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path, clock=FakeClock())
    x.pat = _setup(x)
    yield x
    x.eng.dispose()


def _plan(m, action="copy"):
    out = m.ok(
        m.pat,
        "propose_organize",
        {"scope": {"filters": {}}, "action": action, "target": {"source": "sorted"}, "path_template": TPL},
    )
    return out["plan_id"]


def _files_untouched(m):
    assert m.sql("select count(*) from worker_task where kind in ('apply','undo')").scalar() == 0


@pytest.mark.integration
@pytest.mark.tc("MCP-APPR-001")
def test_appr_001_apply_only_creates_a_request(m):
    pid = _plan(m)
    new = m.ok(m.pat, "apply_organize", {"plan_id": pid}, mcp_protocol_version="2026-07-28")
    assert new["status"] == "input_required" and new["approval_url"].endswith(f"/approvals/{new['approval_id']}")
    raw = m.call(m.pat, "apply_organize", {"plan_id": pid}, mcp_protocol_version="2026-07-28")["result"]
    assert raw["resultType"] == "input_required" and raw["inputRequests"][0]["mode"] == "url"
    old = m.ok(m.pat, "apply_organize", {"plan_id": pid}, mcp_protocol_version="2025-11-25")
    assert old["status"] == "approval_pending" and old["approval_id"] == new["approval_id"]  # one open request
    a = m.sql("select * from approval where id=:i", i=new["approval_id"]).one()
    assert a.status == "pending" and (a.expires_at - a.created_at) == timedelta(minutes=30)
    _files_untouched(m)
    assert m.sql("select status from output_plan where id=:p", p=pid).scalar() == "draft"


@pytest.mark.security
@pytest.mark.tc("MCP-APPR-002")
def test_appr_002_no_token_can_approve(m):
    pid = _plan(m)
    aid = m.ok(m.pat, "apply_organize", {"plan_id": pid})["approval_id"]
    every = m.token(["photos:read", "photos:media", "labels:write", "jobs:run", "organize:propose"])
    for pat in (m.pat, every):
        for headers in ({"Authorization": f"Bearer {pat}"}, {"Authorization": f"Bearer {pat}", "X-OPA-UI": "1"}):
            r = m.client.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "approve"}, headers=headers)
            assert r.status_code in (401, 403), r.text
    r = m.client.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "approve"})  # no CSRF header
    assert r.status_code == 403
    assert m.call(m.pat, "approve", {})["error"]["code"] == -32601  # and there is no such tool
    _files_untouched(m)


@pytest.mark.integration
@pytest.mark.tc("MCP-APPR-003")
def test_appr_003_desktop_gets_a_deep_link_and_a_notification_event(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path, launch_token="lt")
    pat = _setup(x)
    pid = x.ok(pat, "propose_organize", {"scope": {"filters": {}}, "action": "copy", "target": {"source": "sorted"},
                                          "path_template": TPL})["plan_id"]
    out = x.ok(pat, "apply_organize", {"plan_id": pid})
    assert out["approval_url"] == f"openphotoagent://approvals/{out['approval_id']}"
    ev = x.sql("select payload from event where kind='approval.requested'").scalars().all()
    assert ev and ev[-1]["approval_id"] == out["approval_id"] and ev[-1]["client"] == "Claude Desktop"
    x.eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("MCP-APPR-004")
def test_appr_004_changed_plan_invalidates_the_approval(m):
    pid = _plan(m)
    aid = m.ok(m.pat, "apply_organize", {"plan_id": pid})["approval_id"]
    m.sql("update output_action set dst_rel = 'elsewhere/' || dst_rel where plan_id = :p and seq = 0", p=pid)
    r = m.client.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "approve"}, headers=m.ui)
    assert r.status_code == 409 and r.json()["code"] == "PLAN_STALE"
    out = m.call(m.pat, "apply_organize", {"plan_id": pid})["result"]
    assert out["isError"] and out["structuredContent"]["code"] == "PLAN_STALE"
    _files_untouched(m)


@pytest.mark.integration
@pytest.mark.tc("MCP-APPR-005")
def test_appr_005_requests_expire_after_30_minutes(m):
    pid = _plan(m)
    aid = m.ok(m.pat, "apply_organize", {"plan_id": pid})["approval_id"]
    m.client.app.state.opa.clock.advance(31 * 60)
    assert m.client.get(f"/api/v1/approvals/{aid}", headers=m.ui).json()["status"] == "expired"
    r = m.client.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "approve"}, headers=m.ui)
    assert r.status_code == 409 and r.json()["code"] == "APPROVAL_EXPIRED"
    again = m.ok(m.pat, "apply_organize", {"plan_id": pid})
    assert again["approval_id"] != aid and again["status"] in ("input_required", "approval_pending")
    _files_untouched(m)


@pytest.mark.integration
@pytest.mark.tc("MCP-APPR-006")
def test_appr_006_approval_view_names_the_requester(m):
    pid = _plan(m, "move")
    aid = m.ok(m.pat, "apply_organize", {"plan_id": pid})["approval_id"]
    v = m.client.get(f"/api/v1/approvals/{aid}", headers=m.ui).json()
    assert v["client"] == "Claude Desktop" and v["grant"]["number"] >= 1 and v["counts"] == {"move": 3}
    r = m.client.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "approve"}, headers=m.ui)
    assert r.status_code == 409 and r.json()["code"] == "MOVE_NOT_CONFIRMED"  # same second confirmation


@pytest.mark.integration
@pytest.mark.tc("MCP-APPR-007")
def test_appr_007_auto_approve_only_small_non_destructive(m):
    m.settings(auto_approve={"actions": ["copy"], "max_actions": 2})
    pid = _plan(m)  # 3 copies > max 2
    assert m.ok(m.pat, "apply_organize", {"plan_id": pid})["status"] in ("input_required", "approval_pending")
    m.settings(auto_approve={"actions": ["copy"], "max_actions": 500})
    pid2 = _plan(m, "move")
    assert m.ok(m.pat, "apply_organize", {"plan_id": pid2})["status"] in ("input_required", "approval_pending")
    r = m.client.put("/api/v1/mcp/settings", json={"auto_approve": {"actions": ["copy", "move"]}}, headers=m.ui)
    assert r.status_code == 422 and r.json()["code"] == "AUTO_APPROVE_DESTRUCTIVE"
    _files_untouched(m)
    pid3 = _plan(m)  # 3 copies, within 500: the person opted in to exactly this
    out = m.ok(m.pat, "apply_organize", {"plan_id": pid3})
    assert out["status"] == "applying" and out["job_id"]
    row = m.sql("select actor from audit_log where action='approval.apply' order by id desc limit 1").scalar()
    assert row.startswith("auto_approve:")


@pytest.mark.integration
@pytest.mark.tc("MCP-APPR-008")
def test_appr_008_retry_after_approval_reports_applying(m):
    pid = _plan(m)
    aid = m.ok(m.pat, "apply_organize", {"plan_id": pid})["approval_id"]
    r = m.client.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "approve"}, headers=m.ui)
    assert r.status_code == 200, r.text
    out = m.ok(m.pat, "apply_organize", {"plan_id": pid})
    assert out["status"] == "applying" and out["job_id"]
    st = m.ok(m.pat, "get_job_status", {"job_id": out["job_id"]})
    assert st["status"] == "applying" and "state" in st["eta"]
    assert m.sql("select count(*) from worker_task where kind='apply'").scalar() == 1


@pytest.mark.integration
@pytest.mark.tc("MCP-APPR-001")
def test_undo_is_also_only_a_request(m):
    pid = _plan(m)
    out = m.call(m.pat, "undo_organize", {"plan_id": pid})["result"]
    assert out["isError"] and out["structuredContent"]["code"] == "PLAN_NOT_APPLIED"
    m.sql("update output_plan set status='applied' where id=:p", p=pid)
    req = m.ok(m.pat, "undo_organize", {"plan_id": pid})
    assert req["status"] in ("input_required", "approval_pending")
    assert m.sql("select count(*) from worker_task where kind='undo'").scalar() == 0

