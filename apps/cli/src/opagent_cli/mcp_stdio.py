"""``opagent mcp``: the stdio MCP server an AI client starts (05 §3.1, §3.10; MCP-TRN-001/002).

A thin relay: each JSON-RPC line from stdin is POSTed to the running app's ``/mcp/v1/rpc`` with the local PAT,
and the answer is written back as one line. It opens no database connection, reads no photo and never talks to
the worker; everything is decided by the API. The app's port can change between launches, so the address is
read from ``<data dir>/run/api.json`` again whenever a request cannot connect.

Local options can only narrow what the token allows: ``--readonly`` hides every tool that is not read-only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import IO, Any

import httpx

READONLY_OK = None  # filled from tools/list annotations


def _server(home: Path, explicit: str | None) -> str | None:
    if explicit:
        return explicit.rstrip("/")
    env = os.environ.get("OPAGENT_SERVER")
    if env:
        return env.rstrip("/")
    run = home / "run" / "api.json"
    try:
        info = json.loads(run.read_text())
    except (OSError, ValueError):
        return None
    return f"http://127.0.0.1:{int(info['port'])}"


def _err(rid: Any, code: int, msg: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}


class Relay:
    def __init__(self, home: Path, token: str | None, server: str | None = None, readonly: bool = False) -> None:
        self.home, self.token, self.explicit, self.readonly = home, token, server, readonly
        self.client_info: dict[str, Any] | None = None
        self.readonly_tools: set[str] = set()
        self.http = httpx.Client(timeout=120)

    def _post(self, msg: dict[str, Any]) -> httpx.Response | None:
        headers = {"Authorization": f"Bearer {self.token}", "X-OPA-Transport": "stdio"}
        if self.client_info and self.client_info.get("name"):
            headers["X-OPA-Client-Name"] = str(self.client_info["name"])[:100]
        for _attempt in range(2):  # a restarted app has a new port: look it up again once
            base = _server(self.home, self.explicit)
            if base is None:
                return None
            try:
                return self.http.post(f"{base}/mcp/v1/rpc", json=msg, headers=headers)
            except httpx.TransportError:
                continue
        return None

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        rid = msg.get("id")
        if not self.token:
            return _err(
                rid, -32001, "沒有 OpenPhotoAgent 存取權杖：請在 app 的「連接 AI 助手」建立，或執行 opagent login"
            )
        method = msg.get("method")
        params = dict(msg.get("params") or {})
        if method == "initialize":
            self.client_info = params.get("clientInfo") or None
        if self.client_info:
            meta = dict(params.get("_meta") or {})
            meta.setdefault("io.modelcontextprotocol/clientInfo", self.client_info)
            params["_meta"] = meta
            msg = {**msg, "params": params}
        if self.readonly and method == "tools/call" and params.get("name") not in self.readonly_tools:
            self.handle({"jsonrpc": "2.0", "id": "_ro", "method": "tools/list"})
            if params.get("name") not in self.readonly_tools:
                return _err(rid, -32601, f"tool not found: {params.get('name')}")
        r = self._post(msg)
        if r is None:
            return None if "id" not in msg else _err(rid, -32002, "OpenPhotoAgent 沒有在執行：請先開啟 app")
        if r.status_code == 401:
            return _err(rid, -32001, "存取權杖無效或已撤銷：請在 app 的「連接 AI 助手」重新建立")
        if r.status_code == 202 or "id" not in msg:
            return None
        out = r.json()
        if method == "tools/list" and "result" in out:
            tools = out["result"].get("tools") or []
            self.readonly_tools = {t["name"] for t in tools if (t.get("annotations") or {}).get("readOnlyHint")}
            if self.readonly:
                out["result"]["tools"] = [t for t in tools if t["name"] in self.readonly_tools]
        return dict(out)


def serve(relay: Relay, stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> None:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            stdout.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            stdout.flush()
            continue
        out = relay.handle(msg) if isinstance(msg, dict) else _err(None, -32600, "invalid request")
        if out is not None:
            stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            stdout.flush()
