"""Human-readable labels for MCP tool errors (06 §7.4; MCP-TRN-005): the agent gets fix *labels* to tell the
user, never fix ids it could try to trigger. Labels come from the same zh-TW catalogue as the UI."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

_LOCALES = (
    Path(__file__).resolve().parent / "_data" / "zh-TW.json"
)  # generated copy (tools/gen_api_labels.py, ADR-0023)


@cache
def _catalog() -> dict[str, Any]:
    try:
        return dict(json.loads(_LOCALES.read_text(encoding="utf-8")))
    except OSError:
        return {}


def label(key: str) -> str:
    node: Any = _catalog()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return key.rsplit(".", 1)[-1].replace("_", " ")
        node = node[part]
    return node if isinstance(node, str) else str(node.get("label", key)) if isinstance(node, dict) else key
