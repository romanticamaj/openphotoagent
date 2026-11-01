"""M0-S1b — kill -9 recovery (docs/09 §6, 08 §9.4 S1).

Full experiment: kill -9 each of postgres / api / worker / llama-server 20 times
under the Tauri supervisor and measure recovery. The supervisor does not exist
until slice 11, so this script currently covers the **postgres** process only:
it writes committed rows, kill -9s the postmaster mid-write-loop, restarts it
(as the supervisor would) and checks recovery time and that every committed row
survived (0 loss).

    uv run python spikes/m0/S1b/run.py --kills 20
    # later, with the desktop shell built:
    uv run python spikes/m0/S1b/run.py --kills 20 --targets postgres,api,worker,llama-server --shell <path>
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import summarize, write_result  # noqa: E402
from pgutil import chown_for_pg, find_pg_bin, pg, wait_ready  # noqa: E402


def psql(bin_dir: Path, port: int, sock: str, sql: str) -> str:
    return pg(bin_dir, "psql", "-h", sock, "-p", str(port), "-U", "opagent", "-d", "postgres", "-tAc", sql).stdout.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kills", type=int, default=20)
    ap.add_argument("--port", type=int, default=54330)
    ap.add_argument("--targets", default="postgres")
    a = ap.parse_args()
    if a.targets != "postgres":
        raise SystemExit("api/worker/llama-server targets need the desktop shell (slice 11)")
    bin_dir = find_pg_bin()
    base = Path(tempfile.mkdtemp(prefix="m0s1b-"))
    chown_for_pg(base)
    data, sock = base / "data", str(base)
    pg(bin_dir, "initdb", "-D", str(data), "-U", "opagent", "-A", "trust", "-E", "UTF8")
    opts = f"-p {a.port} -k {sock} -c listen_addresses=''"
    pg(bin_dir, "pg_ctl", "-D", str(data), "-o", opts, "-l", str(base / "log"), "start")
    wait_ready(bin_dir, a.port, sock)
    psql(bin_dir, a.port, sock, "CREATE TABLE t(id serial primary key, v int)")
    committed = 0
    recov: list[float] = []
    lost_total = 0
    for k in range(a.kills):
        for _ in range(50):
            psql(bin_dir, a.port, sock, f"INSERT INTO t(v) VALUES ({committed})")
            committed += 1
        pid = int((data / "postmaster.pid").read_text().split()[0])
        os.kill(pid, signal.SIGKILL)
        t0 = time.perf_counter()
        # supervisor behaviour: wait for exit, restart (stale postmaster.pid handled by PG itself)
        for _ in range(500):
            try:
                os.kill(pid, 0)
                time.sleep(0.01)
            except ProcessLookupError:
                break
        # children may linger briefly after postmaster SIGKILL
        time.sleep(0.2)
        pg(bin_dir, "pg_ctl", "-D", str(data), "-o", opts, "-l", str(base / "log"), "start", check=False)
        wait_ready(bin_dir, a.port, sock, timeout=60)
        recov.append(time.perf_counter() - t0)
        n = int(psql(bin_dir, a.port, sock, "SELECT count(*) FROM t"))
        lost_total += committed - n
    pg(bin_dir, "pg_ctl", "-D", str(data), "-m", "fast", "stop", check=False)
    s = summarize(recov)
    metrics = {"target": "postgres", "kills": a.kills, "recovery_s": s, "committed_rows": committed, "lost_rows": lost_total}
    ok = s["max"] <= 30 and lost_total == 0
    write_result(
        "M0-S1b",
        status="partial",
        metrics=metrics,
        passed=None if ok else False,
        notes="postgres-only kill -9 + restart on Linux (recovery time includes 0.2 s child-exit wait). "
        "api/worker/llama-server under the Tauri supervisor and Windows/macOS: pending-hardware/pending slice 11.",
    )
    print(metrics)


if __name__ == "__main__":
    main()
