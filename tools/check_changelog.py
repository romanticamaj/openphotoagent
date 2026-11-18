"""Every breaking change ships with a migration note (VERSIONING §4; 09b §9.4 MIG-003).

    uv run python tools/check_changelog.py                  # CHANGELOG.md
    uv run python tools/check_changelog.py --commits A..B   # conventional commits in a range (CI on PRs)

A breaking entry is a bullet under a "BREAKING" heading; a breaking commit has ``!`` after its type/scope or a
``BREAKING CHANGE:`` footer. Each needs ``Migration:`` (release-please copies the commit text into the CHANGELOG).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = re.compile(r"^#{2,4}\s")
BREAKING_HEADING = re.compile(r"^#{2,4}\s.*breaking", re.I)
BULLET = re.compile(r"^\s*[*-]\s+")
BANG = re.compile(r"^[a-z]+(\([^)]*\))?!:")


def check_changelog(text: str) -> list[str]:
    """Breaking bullets that have no ``Migration:`` (a bullet runs until the next bullet or heading)."""
    bad, in_breaking, current = [], False, None

    def close() -> None:
        if current is not None and "Migration:" not in current:
            bad.append(current.strip().splitlines()[0][:200])

    for line in text.splitlines():
        if HEADER.match(line):
            close()
            current = None
            in_breaking = bool(BREAKING_HEADING.match(line))
        elif in_breaking and BULLET.match(line):
            close()
            current = line
        elif in_breaking and current is not None and line.strip():
            current += "\n" + line
    close()
    return bad


def check_commits(messages: list[str]) -> list[str]:
    bad = []
    for m in messages:
        subject = m.strip().splitlines()[0] if m.strip() else ""
        if (BANG.match(subject) or "BREAKING CHANGE:" in m or "BREAKING-CHANGE:" in m) and "Migration:" not in m:
            bad.append(subject[:200])
    return bad


def _commits(rng: str) -> list[str]:
    out = subprocess.run(["git", "log", "--format=%B%x00", rng], capture_output=True, text=True, check=True, cwd=ROOT)
    return [m for m in out.stdout.split("\x00") if m.strip()]


def main(argv: list[str]) -> int:
    if "--commits" in argv:
        bad = check_commits(_commits(argv[argv.index("--commits") + 1]))
    else:
        bad = check_changelog((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    for b in bad:
        print(f"breaking change without a 'Migration:' note: {b}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
