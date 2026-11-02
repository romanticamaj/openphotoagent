"""Argument redaction for ``mcp_call_log`` (05 §3.8; MCP-AUD-002): query text is kept, id lists are counted,
handles are dropped; ``args_hash`` is the SHA-256 of the canonical full arguments."""

from __future__ import annotations

import hashlib
import json
from typing import Any

_HANDLES = {"selection_id", "cursor"}


def _walk(v: Any, key: str = "") -> Any:
    if isinstance(v, dict):
        return {k: _walk(x, k) for k, x in v.items()}
    if isinstance(v, list) and (key.endswith("_ids") or key == "ids"):
        return {"count": len(v)}
    if isinstance(v, list):
        return [_walk(x, key) for x in v]
    if key in _HANDLES and isinstance(v, str):
        return {"redacted": True}
    if isinstance(v, str) and len(v) > 500:
        return v[:500] + "…"
    return v


def redact(args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    canon = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return _walk(args), hashlib.sha256(canon.encode()).hexdigest()
