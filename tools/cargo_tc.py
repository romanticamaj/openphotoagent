"""Run ``cargo test`` for the desktop supervisor and record test-case results in ``.tc/rust.json``.

Rust test names carry their 09a ids: ``desk_003_api_backoff…`` → DESK-003, ``desk_002_desk_004_…`` → both.
Output format matches the pytest plugin: ``{ID: [{nodeid, outcome}]}``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "apps" / "desktop" / "supervisor"
LINE = re.compile(r"^test (\S+) \.\.\. (ok|FAILED|ignored)$")
ID = re.compile(r"(?:^|_)((?:mu_)?[a-z]+)_(\d{3})(?=_|$)")  # mu_deploy_002 → MU-DEPLOY-002
OUTCOME = {"ok": "passed", "FAILED": "failed", "ignored": "skipped"}


def ids_of(name: str) -> list[str]:
    leaf = name.rsplit("::", 1)[-1]
    return [f"{p.upper().replace('_', '-')}-{n}" for p, n in ID.findall(leaf)]


def parse(output: str) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for line in output.splitlines():
        m = LINE.match(line.strip())
        if not m:
            continue
        for tc in ids_of(m.group(1)):
            out.setdefault(tc, []).append({"nodeid": f"rust::{m.group(1)}", "outcome": OUTCOME[m.group(2)]})
    return out


def main() -> int:
    env = dict(os.environ)
    r = subprocess.run(
        ["cargo", "test", "--no-fail-fast", "--", "--test-threads=4"],
        cwd=CRATE,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stdout.write(r.stdout[-4000:])
    sys.stderr.write(r.stderr[-4000:])
    results = parse(r.stdout)
    dest = ROOT / ".tc" / "rust.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(results, indent=1, sort_keys=True))
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
