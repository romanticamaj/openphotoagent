"""Aggregate spikes/m0/results/*.json into a Markdown table (used by m0-spike.yml)."""

from __future__ import annotations

import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"


def main() -> None:
    print("| ID | status | pass | machine | notes |\n|---|---|---|---|---|")
    for f in sorted(RESULTS.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        mach = d.get("machine", {}) or {}
        label = mach.get("hostname_label") or mach.get("label") or ""
        osn = mach.get("os", "")
        p = {True: "✅", False: "❌", None: "—"}[d.get("pass")]
        print(f"| {d['id']} | {d['status']} | {p} | {label} {osn} | {d.get('notes','')[:120]} |")


if __name__ == "__main__":
    main()
