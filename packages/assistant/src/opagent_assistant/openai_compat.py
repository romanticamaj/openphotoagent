"""OpenAI-compatible chat client (10 §8.2): llama-server with ``--jinja`` tool calling on the worker, or an external
endpoint the user configured (cloud mode).

* Messages are plain text; images are never sent (ASK-PRV-002). ``bytes_out`` counts what left for the egress log.
* A tool call is accepted only for a tool we offered and with arguments that parse as a JSON object; anything else
  becomes an empty reply with ``last_error`` set, so the turn falls back to the fast path's facts.
* Timeouts are quiet: an empty reply and ``last_error = "TIMEOUT"``.
* ``tool_calling_ok`` is False until ``probe()`` passes every try — the general loop is off otherwise (ASK-LOOP-003,
  and the M0-S7 rule: below 95 % reliable tool calls, fast path only).
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any
from urllib.parse import urlparse

import httpx

from .llm import Reply

PROBE_TRIES = 3
PROBE_PROMPT = "請呼叫 count_photos 工具計算「貓」的照片數量。只呼叫工具，不要回答文字。"


def is_local(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class OpenAICompatLLM:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        model: str = "default",
        tools: list[dict[str, Any]] | None = None,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        name: str | None = None,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.model = model
        self.name = name or model
        self.tools = {t["name"]: t for t in tools or []}
        self.cloud = not is_local(base_url)
        self.tool_calling_ok = False
        self.bytes_out = 0
        self.last_error: str | None = None
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(timeout=timeout_s, headers=headers, transport=transport)
        self._call_ids: list[str] = []

    # ---- messages ---------------------------------------------------------
    def _body(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        offered: list[str] = []
        out: list[dict[str, Any]] = []
        calls = iter(self._call_ids)
        for m in messages:
            if m.get("tools"):
                offered = [t for t in m["tools"] if t in self.tools]
            if m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": next(calls, "call"), "content": str(m["content"])})
            else:
                out.append({"role": m["role"], "content": str(m.get("content") or "")})
        body: dict[str, Any] = {"model": self.model, "messages": out, "temperature": 0.2, "stream": False}
        if offered:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": n,
                        "description": self.tools[n].get("description", ""),
                        "parameters": self.tools[n].get("inputSchema") or {"type": "object"},
                    },
                }
                for n in offered
            ]
            body["tool_choice"] = "auto"
        return body

    def _post(self, body: dict[str, Any]) -> dict[str, Any] | None:
        raw = json.dumps(body)
        self.bytes_out += len(raw.encode())
        try:
            r = self._http.post(
                f"{self.base}/chat/completions", content=raw, headers={"Content-Type": "application/json"}
            )
            r.raise_for_status()
            return dict(r.json())
        except httpx.TimeoutException:
            self.last_error = "TIMEOUT"
        except (httpx.HTTPError, ValueError):
            self.last_error = "LLM_UNAVAILABLE"
        return None

    def next(self, messages: list[dict[str, Any]]) -> Reply:
        self.last_error = None
        out = self._post(self._body(messages))
        if out is None:
            return Reply(text="")
        msg = ((out.get("choices") or [{}])[0] or {}).get("message") or {}
        calls = msg.get("tool_calls") or []
        if calls:
            fn = (calls[0] or {}).get("function") or {}
            name = str(fn.get("name") or "")
            if name not in self.tools:
                self.last_error = "TOOL_UNKNOWN"
                return Reply(text="")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = None
            if not isinstance(args, dict):
                self.last_error = "TOOL_JSON_INVALID"
                return Reply(text="")
            self._call_ids.append(str(calls[0].get("id") or f"call{len(self._call_ids)}"))
            return Reply(tool=name, args=args)
        return Reply(text=str(msg.get("content") or ""))

    def probe(self) -> bool:
        """Tool calling must work every time on a trivial request before the general loop is allowed."""
        ok = 0
        for _ in range(PROBE_TRIES):
            r = self.next(
                [
                    {"role": "system", "content": "", "tools": ["count_photos"]},
                    {"role": "user", "content": PROBE_PROMPT},
                ]
            )
            if r.tool == "count_photos" and isinstance(r.args.get("query"), str) and r.args["query"]:
                ok += 1
        self._call_ids.clear()
        self.tool_calling_ok = ok == PROBE_TRIES
        return self.tool_calling_ok
