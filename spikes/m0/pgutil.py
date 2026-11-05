"""Locate PostgreSQL binaries and run them (non-root) for M0 PG experiments."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
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
    return subprocess.run(_prefix() + [exe, *args], text=True, encoding="utf-8", errors="replace", capture_output=True, check=check)


def pg_start(bin_dir: Path, *args: str) -> None:
    """`pg_ctl ... start` with no captured pipes: on Windows the postmaster inherits them and a captured run
    waits for EOF forever (same fix as opagent_testing.pg.start_cluster)."""
    subprocess.run(_prefix() + [str(bin_dir / "pg_ctl"), *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True)


def make_base(prefix: str) -> Path:
    """Scratch dir. On Windows mkdtemp's owner-only ACL locks out initdb's restricted token, so use a plain mkdir."""
    if sys.platform == "win32":
        base = Path(tempfile.gettempdir()) / f"{prefix}{uuid.uuid4().hex[:8]}"
        base.mkdir()
        return base
    return Path(tempfile.mkdtemp(prefix=prefix))


def initdb_locale_args(bin_dir: Path) -> list[str]:
    """What the desktop passes to initdb on PG 17 (ADR-0020); Windows has no C.UTF-8 libc locale."""
    out = subprocess.run([str(bin_dir / "initdb"), "--version"], text=True, capture_output=True).stdout
    major = int(out.strip().rsplit(" ", 1)[-1].split(".")[0])
    return ["--locale-provider=builtin", "--builtin-locale=C.UTF-8", "--locale=C"] if major >= 17 else ["--locale=C"]


def wait_ready(bin_dir: Path, port: int, sockdir: str, timeout: float = 60.0) -> float:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        r = pg(bin_dir, "pg_isready", "-h", sockdir, "-p", str(port), check=False)
        if r.returncode == 0:
            return time.perf_counter() - t0
        time.sleep(0.02)
    raise TimeoutError("postgres did not become ready")
