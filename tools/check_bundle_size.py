"""Installer size gate (08 §4.1 D6, DESK-030): every compressed installer ≤ 200 MB, target 150 MB.

    python tools/check_bundle_size.py <artefact> [<artefact> ...]

Exit 0 when all fit, 1 when any is over the hard limit, 2 when an artefact is missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

LIMIT_MB = 200
TARGET_MB = 150
MB = 1024 * 1024


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_bundle_size.py <artefact> ...", file=sys.stderr)
        return 2
    worst = 0
    for a in map(Path, argv):
        if not a.is_file():
            print(f"MISSING {a}")
            worst = max(worst, 2)
            continue
        size = a.stat().st_size
        mb = size / MB
        if size > LIMIT_MB * MB:
            print(f"FAIL {a.name}: {mb:.1f} MB > {LIMIT_MB} MB (hard limit; see ADR-0022 for what to cut)")
            worst = max(worst, 1)
        else:
            note = "within target" if mb <= TARGET_MB else "over target"
            print(f"OK   {a.name}: {mb:.1f} MB ({note}; target {TARGET_MB} MB, limit {LIMIT_MB} MB)")
    return worst


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
