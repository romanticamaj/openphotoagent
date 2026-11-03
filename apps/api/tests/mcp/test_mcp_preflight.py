"""`mcp.connect` preflight (06 §4.2 row `mcp.connect`, §4.7 #60/#61; 09a PF-060, PF-061; 05 §3 `mcp.enabled`):
the Integrations page's "test connection" gets a blocker with its fix, and the switch is enforced by the server,
not only reported."""

import json
from pathlib import Path

import pytest
import yaml
from opagent_testing.mcp_harness import M

ROOT = Path(__file__).resolve().parents[4]
GOLDEN = {r["id"]: r for r in yaml.safe_load((ROOT / "fixtures/golden/preflight_catalog.yaml").read_text())["rows"]}
I18N = {lang: json.loads((ROOT / f"packages/i18n/errors.{lang}.json").read_text()) for lang in ("zh-TW", "en")}


@pytest.fixture
def m(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path)
    yield x
    x.eng.dispose()


def _pf(m, **params):
    r = m.client.post("/api/v1/preflight", json={"action": "mcp.connect", "params": params}, headers=m.ui)
    assert r.status_code == 200, r.text
    return r.json()


def _check(pf, pid):
    row = GOLDEN[pid]
    code = row["codes"][0]
    hit = [b for b in pf["blockers"] if b["code"] == code]
    assert hit, pf
    p = hit[0]
    assert [(f["type"], f.get("target")) for f in p["fixes"]] == [(f["type"], f.get("target")) for f in row["fixes"]]
    assert p["fixes"][0]["primary"] is True
    for lang in ("zh-TW", "en"):
        assert {"title", "message", "cta"} <= set(I18N[lang][code]), (lang, code)
    assert pf["ready"] is False


@pytest.mark.integration
@pytest.mark.tc("PF-060")
def test_pf_060_mcp_disabled_blocks_connect_and_the_server(m):
    pat = m.token()
    pf = _pf(m, token_id=m.last_token["id"])
    assert pf["ready"] is True and not pf["blockers"], pf  # on by default (05 §3: a workspace may turn it off)
    m.settings(enabled=False)
    assert m.client.get("/api/v1/mcp/settings", headers=m.ui).json().get("enabled") is False
    _check(_pf(m, token_id=m.last_token["id"]), "PF-060")
    r = m.rpc(pat, "tools/list")  # the switch is enforced, not just shown
    assert r.status_code == 403 and r.json()["code"] == "MCP_DISABLED"
    assert m.rpc(None, "tools/list").json()["code"] == "MCP_DISABLED"  # no hint about whether a token is valid
    m.settings(enabled=True)
    assert m.rpc(pat, "tools/list").status_code == 200
    assert _pf(m, token_id=m.last_token["id"])["ready"] is True


@pytest.mark.integration
@pytest.mark.tc("PF-061")
def test_pf_061_revoked_token_blocks_connect_with_regenerate(m):
    m.token()
    tid = m.last_token["id"]
    assert _pf(m, token_id=tid)["ready"] is True
    m.client.delete(f"/api/v1/mcp/tokens/{tid}", headers=m.ui)
    pf = _pf(m, token_id=tid)
    _check(pf, "PF-061")
    assert [b["code"] for b in pf["blockers"]] == ["MCP_TOKEN_INVALID"]
    _check(_pf(m, token_id="tok-that-never-existed"), "PF-061")
