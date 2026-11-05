"""``opagent mcp`` and the local token (09b §3.7 MCP-TRN, MCP-PAT)."""

import io
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import uvicorn
from opagent_api.app import create_app
from opagent_testing.mcp_harness import M
from opagent_testing.perms import assert_owner_only


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Live:
    """The API on a real port, like the desktop sidecar, with ``run/api.json`` written for ``opagent mcp``."""

    def __init__(self, dsn, home: Path):
        self.dsn, self.home = dsn, home
        self.server = None

    def start(self):
        self.port = _free_port()
        cfg = uvicorn.Config(create_app(dsn=self.dsn), host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        while not self.server.started:
            self.thread.join(0.02)
        (self.home / "run").mkdir(parents=True, exist_ok=True)
        (self.home / "run" / "api.json").write_text(
            json.dumps({"port": self.port, "pid": os.getpid()}), encoding="utf-8"
        )

    def stop(self):
        self.server.should_exit = True
        self.thread.join(10)


@pytest.fixture
def live(embedded_pg, tmp_path):
    m = M(embedded_pg.dsn, tmp_path)
    m.add("2025/a.jpg", caption="貓", tags=("animal.cat",))
    m.index()
    pat = m.token()
    lv = Live(embedded_pg.dsn, tmp_path / "home")
    lv.start()
    yield lv, pat
    lv.stop()
    m.eng.dispose()


def _session(home, pat, lines, *extra):
    env = {**os.environ, "OPAGENT_HOME": str(home), "OPAGENT_TOKEN": pat}
    env.pop("OPAGENT_SERVER", None)
    p = subprocess.run(
        [sys.executable, "-m", "opagent_cli", "mcp", *extra],
        input="".join(json.dumps(x) + "\n" for x in lines),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert p.returncode == 0, p.stderr
    return [json.loads(x) for x in p.stdout.splitlines() if x.strip()]


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "Claude Desktop", "version": "1"}},
}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


@pytest.mark.integration
@pytest.mark.tc("MCP-TRN-001")
def test_trn_001_finds_the_new_port_after_a_restart(live):
    lv, pat = live
    first = _session(lv.home, pat, [INIT, LIST])
    assert first[1]["result"]["tools"]
    lv.stop()
    lv.start()  # the app comes back on another port; the client config is unchanged
    again = _session(lv.home, pat, [INIT, LIST])
    assert again[1]["result"]["tools"] == first[1]["result"]["tools"]


@pytest.mark.security
@pytest.mark.tc("MCP-TRN-002")
def test_trn_002_stdio_is_a_thin_client():
    code = (  # run the real entry point (empty stdin ends the session), then look at what got imported
        "import io, sys; sys.stdin = io.StringIO('');"
        "from opagent_cli.main import main; main(['mcp', '--home', '/nonexistent']);"
        "bad=[m for m in sys.modules if m.split('.')[0] in "
        "('sqlalchemy','psycopg','opagent_db','opagent_worker','opagent_fs','opagent_inference','PIL','opagent_api')];"
        "print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


@pytest.mark.contract
@pytest.mark.tc("MCP-TRN-003", "MCP-TRN-004")
def test_trn_003_004_both_protocol_versions_and_stable_lists(live):
    lv, pat = live
    for v in ("2026-07-28", "2025-11-25"):
        init = {**INIT, "params": {**INIT["params"], "protocolVersion": v}}
        disc = {"jsonrpc": "2.0", "id": 3, "method": "server/discover", "params": {"protocolVersion": v}}
        call = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "list_tags", "arguments": {}, "protocolVersion": v},
        }
        out = _session(lv.home, pat, [init, LIST, disc, call, LIST])
        assert out[0]["result"]["protocolVersion"] == v
        assert v in out[2]["result"]["supportedVersions"]
        assert out[3]["result"]["isError"] is False
        assert json.dumps(out[1]["result"]).encode() == json.dumps(out[4]["result"]).encode()


@pytest.mark.contract
@pytest.mark.tc("MCP-TRN-005")
def test_trn_005_errors_carry_codes_and_labels_not_fix_ids(embedded_pg, tmp_path, monkeypatch):
    import opagent_api.mcp_tools as t

    m = M(embedded_pg.dsn, tmp_path)
    pat = m.token()

    def boom(ctx, args):
        raise t.ToolError("SEARCH_INDEX_INCOMPLETE", "索引尚未完成")

    monkeypatch.setattr(t, "search_photos", boom)
    res = m.call(pat, "search_photos", {"query": "貓"})["result"]
    sc = res["structuredContent"]
    assert res["isError"] and sc["code"] == "SEARCH_INDEX_INCOMPLETE"
    assert sc["fixes"] and all(set(f) == {"label"} for f in sc["fixes"])
    assert "fix_id" not in json.dumps(res) and "SEARCH_INDEX_INCOMPLETE." not in json.dumps(res)
    m.eng.dispose()


@pytest.mark.integration
@pytest.mark.tc("MCP-TRN-001")
def test_readonly_flag_hides_write_tools(live):
    from fastapi.testclient import TestClient

    lv, _ = live
    c = TestClient(create_app(dsn=lv.dsn), base_url="http://127.0.0.1:8080")
    pat = c.post(
        "/api/v1/mcp/tokens", json={"name": "rw", "scopes": ["photos:read", "labels:write"]}, headers={"X-OPA-UI": "1"}
    ).json()["token"]
    out = _session(lv.home, pat, [INIT, LIST], "--readonly")
    names = {t["name"] for t in out[1]["result"]["tools"]}
    assert "update_labels" not in names and "search_photos" in names
    call = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "update_labels", "arguments": {}}}
    assert _session(lv.home, pat, [INIT, call], "--readonly")[1]["error"]["code"] == -32601


@pytest.mark.security
@pytest.mark.tc("MCP-PAT-001")
def test_pat_001_keyring_first_file_fallback(tmp_path, monkeypatch):
    import keyring
    from keyring.backend import KeyringBackend
    from opagent_mcp import credstore

    class Mem(KeyringBackend):
        priority = 1
        store: dict = {}

        def set_password(self, s, u, p):
            self.store[(s, u)] = p

        def get_password(self, s, u):
            return self.store.get((s, u))

        def delete_password(self, s, u):
            self.store.pop((s, u), None)

    monkeypatch.delenv("OPAGENT_TOKEN", raising=False)
    prev = keyring.get_keyring()
    try:
        keyring.set_keyring(Mem())
        r = credstore.save("opagent_pat_secret", tmp_path)
        assert r["store"] == "keyring" and credstore.load(tmp_path) == "opagent_pat_secret"
        assert not list(tmp_path.rglob("*mcp-pat*"))
        from keyring.backends.fail import Keyring as Fail

        keyring.set_keyring(Fail())
        r = credstore.save("opagent_pat_other", tmp_path)
        assert r == {"store": "file", "warning": "KEYRING_UNAVAILABLE", "path": r["path"]}
        assert_owner_only(r["path"], 0o600)
        assert credstore.load(tmp_path) == "opagent_pat_other"
    finally:
        keyring.set_keyring(prev)


@pytest.mark.unit
@pytest.mark.tc("MCP-PAT-003")
def test_pat_003_client_config_uses_an_absolute_path(tmp_path, monkeypatch):
    from opagent_api import mcp_clients

    exe = tmp_path / "Program Files" / "OpenPhotoAgent" / "bin" / "opagent"
    monkeypatch.setenv("OPAGENT_CLI_PATH", str(exe))
    monkeypatch.setenv("OPAGENT_CLIENT_CONFIG_HOME", str(tmp_path / "home"))
    for client in mcp_clients.CLIENTS:
        p = mcp_clients.proposal(client)
        assert Path(p["after"]["command"]).is_absolute() and p["after"]["command"] == str(exe)
        assert p["after"]["args"] == ["mcp"] and "token" not in json.dumps(p["content"]).lower()


@pytest.mark.unit
@pytest.mark.tc("MCP-TRN-003")
def test_stdin_loop_handles_garbage():
    from opagent_cli.mcp_stdio import Relay, serve

    out = io.StringIO()
    serve(Relay(Path("/nonexistent"), "opagent_pat_x", server="http://127.0.0.1:9"), io.StringIO("not json\n"), out)
    assert json.loads(out.getvalue())["error"]["code"] == -32700


@pytest.mark.unit
@pytest.mark.tc("MCP-TRN-008")
def test_trn_008_stdio_is_utf8_whatever_the_code_page():
    """回歸: on Windows stdout defaulted to cp1252 and the first Chinese caption killed `opagent mcp`."""
    code = "from opagent_cli.mcp_stdio import utf8_stdio; utf8_stdio(); print('貓')"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    r = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", code], capture_output=True, env={**env, "PYTHONIOENCODING": "cp1252"}
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "貓\n".encode()
