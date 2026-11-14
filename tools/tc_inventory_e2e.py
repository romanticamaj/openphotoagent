#!/usr/bin/env python3
"""Playwright inventory for tc_status --verify (ADR-0021): `playwright test --list --reporter=json` on stdin →
{ID: [{nodeid, tier}]} on stdout. Tier = the project: chromium → pr, visual → visual; --tier overrides (perf)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

ID = re.compile(r"\[([A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}[a-z]?)\]")
TIER = {"chromium": "pr", "visual": "visual"}


def walk(suite: dict[str, Any], out: dict[str, list[dict[str, str]]], tier: str | None) -> None:
    for spec in suite.get("specs", []):
        for test in spec.get("tests", []):
            t = tier or TIER.get(test.get("projectName", ""), "pr")
            for i in ID.findall(spec.get("title", "")):
                out.setdefault(i, []).append({"nodeid": f"{spec.get('file', '')}::{spec['title']}", "tier": t})
    for s in suite.get("suites", []):
        walk(s, out, tier)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier")
    a = ap.parse_args()
    data = json.load(sys.stdin)
    out: dict[str, list[dict[str, str]]] = {}
    for s in data.get("suites", []):
        walk(s, out, a.tier)
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
