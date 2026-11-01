"""Locate PostgreSQL binaries and run them (non-root) for M0 PG experiments."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from pathlib import Path


def find_pg_bin() -> Path:
    env = os.environ.get("OPAGENT_PG_BIN")
    if env:
        return Path(env)
    pgc = shutil.which("pg_config")
    if pgc:
        out = subprocess.run([pgc, "--bindir"], text=True, capture_output=True).stdout.strip()
        if out and Path(out, "initdb").exists():
            return Path(out)
    cands = sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True)
    for c in cands:
        if Path(c, "initdb").exists():
            return Path(c)
    for c in ("/opt/homebrew/opt/postgresql@17/bin", r"C:\Program Files\PostgreSQL\17\bin"):
        if Path(c).exists():
            return Path(c)
    raise SystemExit("PostgreSQL binaries not found; set OPAGENT_PG_BIN")


def _prefix() -> list[str]:
    # postgres refuses to run as root; drop to the 'postgres' user when needed.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        user = os.environ.get("OPAGENT_PG_USER", "postgres")
        return ["runuser", "-u", user, "--"]
    return []


def chown_for_pg(path: Path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        user = os.environ.get("OPAGENT_PG_USER", "postgres")
        shutil.chown(path, user, user)


def pg(bin_dir: Path, tool: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    exe = str(bin_dir / tool)
    return subprocess.run(_prefix() + [exe, *args], text=True, capture_output=True, check=check)


def wait_ready(bin_dir: Path, port: int, sockdir: str, timeout: float = 60.0) -> float:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        r = pg(bin_dir, "pg_isready", "-h", sockdir, "-p", str(port), check=False)
        if r.returncode == 0:
            return time.perf_counter() - t0
        time.sleep(0.02)
    raise TimeoutError("postgres did not become ready")
