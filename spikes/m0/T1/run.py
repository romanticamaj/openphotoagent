"""M0-T1 — test infrastructure feasibility (docs/09 §6).

(a) embedded_pg template clone time: CREATE DATABASE ... TEMPLATE x, p95 < 200 ms
(b) tiny GGUF/ONNX model selection for PR contract tests (< 50 MB, < 2 s CPU)
(c) Windows runner: junction, second drive (subst/VHD), RECALL_ON_DATA_ACCESS
(d) Playwright on desktop build with mocked backend_info()

Only (a) is measured by this script on any OS. (b) needs Hugging Face access
(blocked in the cloud session); (c) needs a Windows runner (see c_windows.ps1);
(d) is verified once apps/web exists (slice 1/10) by the e2e job.

    uv run python spikes/m0/T1/run.py --clones 100
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import summarize, write_result  # noqa: E402
from pgutil import chown_for_pg, find_pg_bin, pg, wait_ready  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clones", type=int, default=100)
    ap.add_argument("--port", type=int, default=54331)
    a = ap.parse_args()
    bin_dir = find_pg_bin()
    base = Path(tempfile.mkdtemp(prefix="m0t1-"))
    chown_for_pg(base)
    data, sock = base / "data", str(base)
    pg(bin_dir, "initdb", "-D", str(data), "-U", "opagent", "-A", "trust", "--no-sync", "-E", "UTF8")
    opts = f"-p {a.port} -k {sock} -c listen_addresses='' -c fsync=off"
    pg(bin_dir, "pg_ctl", "-D", str(data), "-o", opts, "-l", str(base / "log"), "start")
    wait_ready(bin_dir, a.port, sock)

    def q(sql: str, db: str = "postgres") -> None:
        pg(bin_dir, "psql", "-h", sock, "-p", str(a.port), "-U", "opagent", "-d", db, "-qtAc", sql)

    q("CREATE DATABASE tmpl")
    # a schema roughly the size of the v0.1 schema (~30 tables with indexes)
    ddl = ";".join(
        f"CREATE TABLE t{i}(id bigserial primary key, ws uuid, name text, meta jsonb, created timestamptz default now());"
        f"CREATE INDEX ON t{i}(ws, created)"
        for i in range(30)
    )
    q(ddl, db="tmpl")
    times: list[float] = []
    for i in range(a.clones):
        # measured via psql round-trip, so includes client process start; reported as upper bound
        t0 = time.perf_counter()
        q(f"CREATE DATABASE c{i} TEMPLATE tmpl")
        times.append((time.perf_counter() - t0) * 1000)
        q(f"DROP DATABASE c{i}")
    pg(bin_dir, "pg_ctl", "-D", str(data), "-m", "fast", "stop", check=False)
    s = summarize(times)
    metrics = {
        "a_template_clone_ms": s,
        "a_note": "includes psql client spawn per call (upper bound); 30-table schema; fsync=off as in test profile",
        "b_tiny_models": "pending: huggingface.co blocked by cloud network policy; candidates listed in README",
        "c_windows_fs": "pending-hardware: run c_windows.ps1 on windows-latest runner",
        "d_playwright_desktop_mock": "pending-implementation: verified by e2e-smoke once apps/web exists",
    }
    write_result(
        "M0-T1",
        status="partial",
        metrics=metrics,
        passed=None if s["p95"] < 200 else False,
        notes="(a) measured on Linux; macOS/Windows runners pending (run via m0-spike.yml).",
    )
    print(metrics)


if __name__ == "__main__":
    main()
