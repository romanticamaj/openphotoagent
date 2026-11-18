"""What the wizard writes has to start from an AI client on a machine with no ``opagent`` on PATH."""

import sys
from pathlib import Path

import pytest
from opagent_api import mcp_clients


@pytest.mark.unit
@pytest.mark.tc("MCP-PAT-007")
def test_pat_007_installed_app_entry_can_really_be_started(monkeypatch, tmp_path):
    r"""回歸: the wizard wrote `...\opagent_cli\__main__.py mcp`, which Windows cannot execute, and no --home, so the
    relay looked for run/api.json in ~/.opagent and never found the app."""
    monkeypatch.delenv("OPAGENT_CLI_PATH", raising=False)
    monkeypatch.setattr(mcp_clients.shutil, "which", lambda _n: None)
    e = mcp_clients.entry(tmp_path)
    assert e["command"] == sys.executable
    assert e["args"] == ["-I", "-m", "opagent_cli", "mcp", "--home", str(tmp_path)]


@pytest.mark.unit
@pytest.mark.tc("MCP-PAT-007")
def test_pat_007_a_cli_on_path_is_used_by_its_absolute_path(monkeypatch, tmp_path):
    exe = tmp_path / "opagent"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setenv("OPAGENT_CLI_PATH", str(exe))
    e = mcp_clients.entry(Path("data"))
    assert e == {"command": str(exe.resolve()), "args": ["mcp", "--home", "data"]}
