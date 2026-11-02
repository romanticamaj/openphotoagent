"""MCP-SCOPE (09b §3.2; ADR-0009): tools/list is exactly the callable set; anything else is unknown (-32601) and
logged as denied with the reason; switches and credentials behave as specified."""

import pytest
from opagent_mcp.scopes import ALL_SCOPES
from opagent_mcp.tools import SERVER_VERSION, TOOLS

from opagent_testing.mcp_harness import M

V02 = sorted(t.name for t in TOOLS if t.available_from <= SERVER_VERSION)
LATER = sorted(t.name for t in TOOLS if t.available_from > SERVER_VERSION)
ISSUABLE = [s for s in ALL_SCOPES if s != "organize:apply"]


@pytest.fixture
def m(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path)
    x.add("2025/kyoto/cat.jpg", caption="貓", tags=("animal.cat",))
    x.index()
    yield x
    x.eng.dispose()


def _apply_token(m):
    """organize:apply is never issued by the UI; tests that need it write the row like a future v0.5 grant would."""
    pat = m.token(["organize:propose"])
    m.sql("update mcp_token set scopes = array_append(scopes, 'organize:apply') where id = :i", i=m.last_token["id"])
    return pat


@pytest.mark.integration
@pytest.mark.tc("MCP-SCOPE-001")
def test_scope_001_end_to_end_lists_and_denials(m):
    m.settings(allow_apply_requests=True, faces_enabled=True)
    by_scope = {t.name: t.scope for t in TOOLS}
    tokens = [(s, [s]) for s in ISSUABLE] + [("organize:apply", None), ("all", ISSUABLE + ["organize:apply"])]
    for label, scopes in tokens:
        if scopes is None or "organize:apply" in scopes:
            pat = m.token(["photos:read"])
            have = set(scopes or ["organize:apply"])
            m.sql("update mcp_token set scopes = :s where id = :i", s=sorted(have), i=m.last_token["id"])
        else:
            pat = m.token(scopes)
            have = set(scopes) | {"photos:read"}  # the UI always includes read
        listed = [t["name"] for t in m.list_tools(pat)]
        want = sorted(n for n in V02 if by_scope[n] in have)
        assert listed == want, label
        for name in V02 + LATER:
            out = m.call(pat, name, {})
            if name in want:
                assert "result" in out, (label, name, out)  # past authorisation (arguments may still be invalid)
            else:
                assert out["error"]["code"] == -32601, (label, name)
        denied = m.sql(
            "select tool, error_code from mcp_call_log where status = 'denied' and token_id = :t",
            t=m.last_token["id"],
        ).all()
        reasons = {t: e for t, e in denied}
        for name in LATER:
            assert reasons[name] == "not_available"
        for name in V02:
            if name not in want:
                assert reasons[name] == "insufficient_scope", (label, name)


@pytest.mark.integration
@pytest.mark.tc("MCP-SCOPE-001")
def test_scope_001_no_scope_token_sees_nothing(m):
    pat = m.token(["photos:read"])
    m.sql("update mcp_token set scopes = '{}' where id = :i", i=m.last_token["id"])
    assert m.list_tools(pat) == []
    assert m.call(pat, "search_photos", {})["error"]["code"] == -32601


@pytest.mark.integration
@pytest.mark.tc("MCP-SCOPE-002")
def test_scope_002_default_token_is_read_only(m):
    m.token()
    assert m.last_token["scopes"] == ["photos:read"]
    r = m.client.post(
        "/api/v1/mcp/tokens", json={"name": "x", "scopes": ["photos:read", "organize:apply"]}, headers=m.ui
    )
    assert r.status_code == 422 and r.json()["code"] == "SCOPE_NEVER_ISSUED"


@pytest.mark.integration
@pytest.mark.tc("MCP-SCOPE-003")
def test_scope_003_read_only_cannot_write(m):
    pat = m.token(["photos:read"])
    out = m.call(pat, "update_labels", {"id": "a00001", "add_tags": ["spam"]})
    assert out["error"]["code"] == -32601
    row = m.calls()[-1]
    assert row.status == "denied" and row.error_code == "insufficient_scope"
    assert m.sql("select count(*) from review").scalar() == 0


@pytest.mark.integration
@pytest.mark.tc("MCP-SCOPE-004")
def test_scope_004_faces_double_gate(m):
    pat = m.token(["photos:read", "faces:read"])
    names = [t["name"] for t in m.list_tools(pat)]
    assert not {"list_people", "get_person", "find_photos_of_person"} & set(names)
    out = m.call(pat, "list_people", {})
    assert out["result"]["isError"] and out["result"]["structuredContent"]["code"] == "MCP_FACES_DISABLED"
    res = m.ok(pat, "search_photos", {"query": "貓"})["results"]
    assert res and all("people" not in r for r in res)  # absent, not an empty list
    bad = m.call(pat, "search_photos", {"filters": {"persons_all": ["p1"]}})
    assert bad["result"]["isError"] and bad["result"]["structuredContent"]["code"] == "MCP_INSUFFICIENT_SCOPE"


@pytest.mark.integration
@pytest.mark.tc("MCP-SCOPE-005")
def test_scope_005_faces_read_without_media(m):
    m.settings(faces_enabled=True)
    pat = m.token(["photos:read", "faces:read"])
    out = m.call(pat, "get_person", {"person_id": "p1"})
    assert out["result"]["isError"]
    assert not [c for c in out["result"]["content"] if c["type"] == "image"]
    r = m.rpc(pat, "resources/read", {"uri": "person://p1/cover"})
    assert r.json()["error"]["code"] == -32602


@pytest.mark.integration
@pytest.mark.tc("MCP-SCOPE-006")
def test_scope_006_apply_requests_switch(m):
    pat = _apply_token(m)
    assert "apply_organize" not in [t["name"] for t in m.list_tools(pat)]
    out = m.call(pat, "apply_organize", {"plan_id": "x"})
    sc = out["result"]["structuredContent"]
    assert out["result"]["isError"] and sc["code"] == "MCP_APPLY_REQUESTS_DISABLED" and "設定" in sc["message"]


@pytest.mark.integration
@pytest.mark.tc("MCP-SCOPE-008")
def test_scope_008_revocation_is_immediate(m):
    pat = m.token()
    assert m.rpc(pat, "tools/list").status_code == 200
    m.client.delete(f"/api/v1/mcp/tokens/{m.last_token['id']}", headers=m.ui)
    r = m.rpc(pat, "tools/list")
    assert r.status_code == 401 and r.json()["code"] == "MCP_TOKEN_INVALID"


@pytest.mark.security
@pytest.mark.tc("MCP-SCOPE-009")
def test_scope_009_launch_token_is_not_an_mcp_credential(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path, launch_token="launch-secret")
    r = x.rpc("launch-secret", "tools/list")
    assert r.status_code == 401
    r = x.client.post(
        "/mcp/v1/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": "Bearer launch-secret"},
    )
    assert r.status_code == 401
    pat = x.token()
    assert x.rpc(pat, "tools/list").status_code == 200  # a PAT works without the launch token
    x.eng.dispose()
