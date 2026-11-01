#!/usr/bin/env python3
"""CI test-integrity + tdd-order (docs/09 §1.3, §1.5).

test-integrity: compared to the base ref, fail when a test function/case is deleted, a skip/xfail/only/fixme
is added, or a thresholds file changes — unless the PR carries the `test-change-approved` label
(env TEST_CHANGE_APPROVED=1).
tdd-order: every feat/fix commit that touches source must be preceded (or accompanied) by a commit
touching tests.

    tools/check_test_integrity.py --base origin/main
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

TEST_FILE = re.compile(r"(^|/)(tests?|conformance|e2e)/|\.test\.[jt]sx?$|\.spec\.[jt]s$|/test_[^/]+\.py$")
SKIP_RE = re.compile(r"pytest\.mark\.(skip|xfail)|\b(it|test|describe)\.(only|skip|fixme)\(|test\.fixme")
DEF_RE = re.compile(r"^-\s*(?:async\s+)?def (test_\w+)|^-\s*(?:it|test)\(\s*[\"'`](.+?)[\"'`]")


def git(*a: str) -> str:
    return subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout


def integrity(base: str) -> list[str]:
    problems: list[str] = []
    diff = git("diff", f"{base}...HEAD", "--unified=0")
    cur = ""
    added_defs: set[str] = set()
    removed: list[tuple[str, str]] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
            continue
        if not TEST_FILE.search(cur):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if SKIP_RE.search(line):
                problems.append(f"{cur}: added skip/xfail/only: {line[1:].strip()}")
            m = re.match(r"^\+\s*(?:async\s+)?def (test_\w+)|^\+\s*(?:it|test)\(\s*[\"'`](.+?)[\"'`]", line)
            if m:
                added_defs.add(m.group(1) or m.group(2))
        m = DEF_RE.match(line)
        if m:
            removed.append((cur, m.group(1) or m.group(2)))
    for f, name in removed:
        if name not in added_defs:
            problems.append(f"{f}: test removed: {name}")
    for f in git("diff", f"{base}...HEAD", "--name-only").splitlines():
        if re.search(r"thresholds\.(py|ts|yaml)$", f):
            problems.append(f"{f}: threshold file changed")
    return problems


def tdd_order(base: str) -> list[str]:
    problems: list[str] = []
    seen_test = False
    for sha in git("rev-list", "--reverse", f"{base}..HEAD").split():
        subj = git("log", "-1", "--format=%s", sha)
        files = git("show", "--name-only", "--format=", sha).split()
        if any(TEST_FILE.search(f) for f in files):
            seen_test = True
        is_src = any(f.endswith((".py", ".ts", ".tsx", ".rs")) and not TEST_FILE.search(f) for f in files)
        if re.match(r"^(feat|fix)(\(|:)", subj) and is_src and not seen_test:
            problems.append(f"{sha[:8]} '{subj}' changes source before any test commit")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    a = ap.parse_args()
    probs = tdd_order(a.base)
    if os.environ.get("TEST_CHANGE_APPROVED") != "1":
        probs += integrity(a.base)
    for p in probs:
        print("::error::" + p)
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
