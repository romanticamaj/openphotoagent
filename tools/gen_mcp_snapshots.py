#!/usr/bin/env python3
"""Write ``tests/contract/mcp/snapshots/<tool>.json`` from the tool registry (09b §3.1 MCP-TOOL).

A changed snapshot is a changed public contract: explain it in the PR (and bump the MCP manifest version).
"""

from __future__ import annotations

import json
from pathlib import Path

from opagent_mcp.tools import TOOLS

OUT = Path(__file__).resolve().parents[1] / "tests/contract/mcp/snapshots"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for t in TOOLS:
        body = json.dumps(t.definition(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        (OUT / f"{t.name}.json").write_text(body, encoding="utf-8")
    print(f"{len(TOOLS)} snapshots in {OUT}")


if __name__ == "__main__":
    main()
