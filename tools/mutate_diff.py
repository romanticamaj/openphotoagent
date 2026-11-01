#!/usr/bin/env python3
"""Mutation testing on changed fs-sandbox / path_template files (docs/09 §5.6; threshold 85 %).

    tools/mutate_diff.py [--base origin/main] [--all]

Runs mutmut only for changed files under the guarded paths and fails if the
mutation score is below 85 %. Equivalent mutants are listed in mutmut-equivalents.txt.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARDED = re.compile(r"^(packages/fs-sandbox/src/.+\.py|packages/core/src/opagent_core/path_template\.py)$")
THRESHOLD = 0.85


def changed(base: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], capture_output=True, text=True, check=True)
    return [f for f in out.stdout.split() if GUARDED.match(f) and (ROOT / f).exists()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    files = (
        [str(p.relative_to(ROOT)) for p in (ROOT / "packages/fs-sandbox/src").rglob("*.py")]
        + ["packages/core/src/opagent_core/path_template.py"]
        if a.all
        else changed(a.base)
    )
    files = [f for f in files if (ROOT / f).exists() and not f.endswith("__init__.py")]
    if not files:
        print("mutate-diff: no guarded files changed")
        return 0
    from mutation_runner import run  # tools/mutation_runner.py

    score, survivors = run(files)
    print(f"mutation score {score:.1%} over {files}")
    for s in survivors[:50]:
        print("  survived:", s)
    return 0 if score >= THRESHOLD else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    sys.exit(main())
