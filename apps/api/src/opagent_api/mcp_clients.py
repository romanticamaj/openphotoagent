"""「連接 AI 助手」 client configs (05 §3.10; 04 §3.10; MCP-PAT-002/003).

The wizard only ever writes the known config file of a known client, shows the change first, keeps a backup,
and writes the absolute path of the bundled ``opagent`` (the installer does not touch PATH). The token is not
in the file: ``opagent mcp`` reads it from the OS keyring.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

CLIENTS = ("claude-desktop", "claude-code", "cursor")
SERVER_KEY = "openphotoagent"


def entry(data_home: Path | None = None) -> dict[str, Any]:
    """How an AI client starts ``opagent mcp``: an absolute path, never a bare command name. The installer ships no
    ``opagent`` executable and leaves PATH alone, so there it is the app's own interpreter (MCP-PAT-007). The data
    folder goes along: the client's working directory and environment say nothing about where the app keeps run/."""
    args = ["mcp", *(["--home", str(data_home)] if data_home else [])]
    cli = os.environ.get("OPAGENT_CLI_PATH") or shutil.which("opagent")
    if cli:
        return {"command": str(Path(cli).resolve()), "args": args}
    return {"command": sys.executable, "args": ["-I", "-m", "opagent_cli", *args]}


def _home() -> Path:
    return Path(os.environ.get("OPAGENT_CLIENT_CONFIG_HOME") or Path.home())


def config_path(client: str, platform: str = sys.platform) -> Path:
    h = _home()
    if client == "claude-desktop":
        if platform == "darwin":
            return h / "Library/Application Support/Claude/claude_desktop_config.json"
        if platform.startswith("win"):
            return Path(os.environ.get("APPDATA") or h / "AppData/Roaming") / "Claude/claude_desktop_config.json"
        return h / ".config/Claude/claude_desktop_config.json"
    if client == "claude-code":
        return h / ".claude.json"
    if client == "cursor":
        return h / ".cursor/mcp.json"
    raise KeyError(client)


def proposal(client: str, data_home: Path | None = None) -> dict[str, Any]:
    path = config_path(client)
    before = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    after = json.loads(json.dumps(before))
    wanted = entry(data_home)
    after.setdefault("mcpServers", {})[SERVER_KEY] = wanted
    return {
        "client": client,
        "path": str(path),
        "exists": path.exists(),
        "before": before.get("mcpServers", {}).get(SERVER_KEY),
        "after": after["mcpServers"][SERVER_KEY],
        "content": after,
        "command_line": shlex.join(["claude", "mcp", "add", SERVER_KEY, "--", wanted["command"], *wanted["args"]])
        if client == "claude-code"
        else None,
    }


def install(client: str, data_home: Path | None = None) -> dict[str, Any]:
    p = proposal(client, data_home)
    path = Path(p["path"])
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".opagent-backup")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".opagent-tmp")
    tmp.write_text(json.dumps(p["content"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return {"path": str(path), "backup": str(backup) if backup else None}
