"""M0-S1a — embedded PostgreSQL start time (docs/09 §6, 08 §9.4 S1, 11 V-1).

Measures cold start (fresh initdb + first start) and warm start (restart of an
existing cluster) N times each, reporting p95 and failures.

Pass (full): warm p95 < 5 s, 0 failures in 50 runs, starts under admin account
on Windows with Defender on. On Linux this only covers the Linux part.

    uv run python spikes/m0/S1a/run.py --runs 50
"""

from __future__ import annotations

import argparse
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import summarize, write_result  # noqa: E402
from pgutil import chown_for_pg, find_pg_bin, initdb_locale_args, make_base, pg, pg_start, wait_ready  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--port", type=int, default=54329)
    ap.add_argument("--id", default="M0-S1a", help="result file id; use a per-machine id so runs do not overwrite each other")
    a = ap.parse_args()
    bin_dir = find_pg_bin()
    version = pg(bin_dir, "postgres", "--version").stdout.strip()
    cold: list[float] = []
    warm: list[float] = []
    failures: list[str] = []
    base = make_base("m0s1a-")
    chown_for_pg(base)
    locale = initdb_locale_args(bin_dir)
    for i in range(a.runs):
        data = base / f"cold{i}"
        sock = base.as_posix()
        try:
            t0 = time.perf_counter()
            pg(bin_dir, "initdb", "-D", str(data), "-U", "opagent", "-A", "trust", "--no-sync", "-E", "UTF8", *locale)
            pg_start(bin_dir, "-D", str(data), "-o", f"-p {a.port} -k {sock} -c listen_addresses=", "-l", str(base / f"cold{i}.log"), "start")
            wait_ready(bin_dir, a.port, sock)
            cold.append(time.perf_counter() - t0)
            pg(bin_dir, "pg_ctl", "-D", str(data), "-m", "fast", "stop")
            t1 = time.perf_counter()
            pg_start(bin_dir, "-D", str(data), "-o", f"-p {a.port} -k {sock} -c listen_addresses=", "-l", str(base / f"warm{i}.log"), "start")
            wait_ready(bin_dir, a.port, sock)
            warm.append(time.perf_counter() - t1)
            pg(bin_dir, "pg_ctl", "-D", str(data), "-m", "fast", "stop")
        except Exception as e:  # noqa: BLE001 - record every failure
            failures.append(f"run {i}: {type(e).__name__}: {getattr(e, 'stderr', e)}"[:300])
            pg(bin_dir, "pg_ctl", "-D", str(data), "-m", "immediate", "stop", check=False)
    metrics = {
        "pg_version": version,
        "runs": a.runs,
        "cold_start_s": summarize(cold) if cold else None,
        "warm_start_s": summarize(warm) if warm else None,
        "failures": len(failures),
        "failure_samples": failures[:5],
        "platform_scope": platform.system(),
    }
    linux_ok = bool(warm) and summarize(warm)["p95"] < 5.0 and not failures
    is_windows = platform.system() == "Windows"
    write_result(
        a.id,
        status="measured" if is_windows else "partial",
        metrics=metrics,
        passed=linux_ok if is_windows or not linux_ok else None,
        notes=(
            "Cold = initdb(--no-sync)+first start until pg_isready; warm = restart existing cluster. "
            + ("" if is_windows else "Linux-only measurement; Windows (normal + admin account, Defender on) and macOS pending-hardware. ")
            + "Desktop ships PG 17; this run used the PG binaries available on the machine (see pg_version)."
        ),
    )
    print(metrics)


if __name__ == "__main__":
    main()
