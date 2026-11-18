"""Launch an installed copy of the app and check that it comes up (DESK-054).

Everything this checks was broken at once when the first real install was run on a real machine: bundles had been built
and size-checked on every commit, and none had ever been opened. Usage: desktop_smoke.py <app executable> [--timeout s]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

BAD_IN_LOGS = ("ModuleNotFoundError", "ImportError", "Traceback")


def problems_in_logs(logs: Path) -> list[str]:
    found = []
    for f in sorted(logs.glob("*")) if logs.is_dir() else []:
        text = f.read_text(encoding="utf-8", errors="replace")
        found += [f"{f.name}: {word}" for word in BAD_IN_LOGS if word in text]
    return found


def wait_for_api(home: Path, alive: Callable[[], bool], timeout_s: float) -> dict[str, object]:
    """The API's health answer, once ``run/api.json`` names a port that responds."""
    deadline = time.monotonic() + timeout_s
    run = home / "run" / "api.json"
    while time.monotonic() < deadline:
        if not alive():
            raise SystemExit("the app exited during start-up")
        try:
            port = json.loads(run.read_text(encoding="utf-8"))["port"]
            url = f"http://127.0.0.1:{port}/healthz"
            with urllib.request.urlopen(url, timeout=2) as r:  # nosec B310 - loopback
                return dict(json.load(r))
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            time.sleep(0.5)
    raise SystemExit(f"no healthy API within {timeout_s:.0f} s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("app", type=Path)
    ap.add_argument("--timeout", type=float, default=180)
    a = ap.parse_args()
    # not mkdtemp: its owner-only ACL on Windows locks out initdb, which drops admin rights on a CI runner
    home = Path(tempfile.gettempdir()) / f"opa-smoke-{os.urandom(6).hex()}"
    home.mkdir(parents=True)
    env = {**os.environ, "OPAGENT_HOME": str(home)}
    app = subprocess.Popen([str(a.app)], env=env)
    code = 1
    try:
        t0 = time.monotonic()
        health = wait_for_api(home, lambda: app.poll() is None, a.timeout)
        print(f"api healthy after {time.monotonic() - t0:.1f} s: {health}")
        time.sleep(5)  # let the worker register, or fail
        bad = problems_in_logs(home / "logs")
        print("\n".join(bad) or "logs are clean")
        code = 1 if bad else 0
    finally:
        app.terminate()
        try:
            app.wait(30)
        except subprocess.TimeoutExpired:
            app.kill()
        for f in sorted((home / "logs").glob("*")) if code else []:
            print(f"--- {f.name}\n{f.read_text(encoding='utf-8', errors='replace')[-3000:]}")
    return code


if __name__ == "__main__":
    sys.exit(main())
