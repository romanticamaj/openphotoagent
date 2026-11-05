"""M0-S1c — Windows AF_UNIX for PostgreSQL clients (docs/09 §6, 08 §1.3).

Connects to PG over a Unix-domain socket 10,000 times with asyncpg and psycopg
and runs a trivial query each time. Pass: 0 errors (then 08 §1.3 may switch the
desktop to AF_UNIX); otherwise keep 127.0.0.1 + SCRAM.

Run on Windows 10 1803+ (pending-hardware) after starting PG with
`unix_socket_directories='C:/opagent/sock'`:
    uv run --with asyncpg,psycopg[binary] python spikes/m0/S1c/run.py --sockdir C:/opagent/sock --port 5432 --user opagent
"""

from __future__ import annotations

import argparse
import asyncio
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import write_result  # noqa: E402


async def run_asyncpg(sockdir: str, port: int, user: str, n: int) -> list[str]:
    import asyncpg

    errs: list[str] = []
    for i in range(n):
        try:
            c = await asyncpg.connect(host=sockdir, port=port, user=user, database="postgres")
            assert await c.fetchval("SELECT 1") == 1
            await c.close()
        except Exception as e:  # noqa: BLE001
            errs.append(f"{i}: {type(e).__name__}: {e}"[:200])
    return errs


def run_psycopg(sockdir: str, port: int, user: str, n: int) -> list[str]:
    import psycopg

    errs: list[str] = []
    for i in range(n):
        try:
            with psycopg.connect(host=sockdir, port=port, user=user, dbname="postgres") as c:
                assert c.execute("SELECT 1").fetchone() == (1,)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{i}: {type(e).__name__}: {e}"[:200])
    return errs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sockdir", required=True)
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--user", default="opagent")
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--asyncpg-n", type=int, help="asyncpg attempts (default --n); it fails structurally on Windows")
    ap.add_argument("--notes", default="")
    a = ap.parse_args()
    n1 = a.n if a.asyncpg_n is None else a.asyncpg_n
    e1 = asyncio.run(run_asyncpg(a.sockdir, a.port, a.user, n1))
    t0 = time.perf_counter()
    e2 = run_psycopg(a.sockdir, a.port, a.user, a.n)
    m = {"n_psycopg": a.n, "n_asyncpg": n1, "asyncpg_errors": len(e1), "psycopg_errors": len(e2),
         "psycopg_connects_per_s": a.n / (time.perf_counter() - t0),
         "asyncpg_samples": e1[:3], "psycopg_samples": e2[:5]}
    is_win = platform.system() == "Windows"
    write_result("M0-S1c", status="measured" if is_win else "partial", metrics=m,
                 passed=(not e1 and not e2) if is_win else None,
                 notes=a.notes if is_win else "non-Windows baseline only; the decision requires a Windows run")
    print(m)


if __name__ == "__main__":
    main()
