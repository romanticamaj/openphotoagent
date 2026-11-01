"""`opagent` CLI (CLAUDE.md §3: CLI name opagent, env prefix OPAGENT_)."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path


def _home() -> Path:
    return Path(os.environ.get("OPAGENT_HOME", Path.home() / ".opagent"))


def _parse_roots(spec: str) -> list[dict[str, str]]:
    """alias=path:mode,alias2=path2:mode"""
    roots = []
    for part in filter(None, spec.split(",")):
        alias, rest = part.split("=", 1)
        path, _, mode = rest.rpartition(":")
        if not path:
            path, mode = rest, "ro"
        roots.append({"alias": alias, "path": path, "mode": mode or "ro"})
    return roots


def cmd_api(a: argparse.Namespace) -> None:
    import uvicorn
    from opagent_api.app import create_app
    from opagent_db.schema import create_all

    create_all(a.dsn)
    app = create_app(dsn=a.dsn, static_dir=a.web_dist)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


def cmd_worker(a: argparse.Namespace) -> None:
    import httpx
    from opagent_worker.agent import WorkerAgent

    client = httpx.Client(base_url=a.api, timeout=30)
    backend = None
    if a.backend == "fake":  # v0.1-B: the FakeBackend until the real adapters land (slice 7)
        from opagent_inference.fake import FakeBackend

        backend = FakeBackend()
    agent = WorkerAgent(
        client, roots=_parse_roots(a.roots), home=_home() / "worker", backend=backend, worker_id=a.worker_id
    )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.is_set():
        try:
            agent.register()
            break
        except httpx.HTTPError:
            stop.wait(0.2)
    if a.once:
        while agent.run_once() or (backend is not None and agent.process_items_once()):
            pass
        return
    agent.run_forever(stop)


def cmd_dev(a: argparse.Namespace) -> None:
    """All-in-one: embedded PG + api (serving the web build) + worker subprocess."""
    from opagent_db.embedded import EmbeddedPg

    pg = EmbeddedPg(_home() / "pg")
    dsn = pg.start()
    worker = subprocess.Popen(
        [sys.executable, "-m", "opagent_cli", "worker", "--api", f"http://127.0.0.1:{a.port}", "--roots", a.roots]
    )
    try:
        cmd_api(argparse.Namespace(dsn=dsn, port=a.port, web_dist=a.web_dist))
    finally:
        worker.terminate()
        worker.wait(10)
        pg.stop()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="opagent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("api")
    p.add_argument("--dsn", required=True)
    p.add_argument("--port", type=int, default=47800)
    p.add_argument("--web-dist")
    p.set_defaults(fn=cmd_api)
    p = sub.add_parser("worker")
    p.add_argument("--api", required=True)
    p.add_argument("--roots", required=True, help="alias=path:mode,...")
    p.add_argument("--worker-id")
    p.add_argument("--backend", default="fake", choices=["fake", "none"])
    p.add_argument("--once", action="store_true", help="process what is queued, then exit")
    p.set_defaults(fn=cmd_worker)
    p = sub.add_parser("dev")
    p.add_argument("--port", type=int, default=47800)
    p.add_argument("--roots", default=os.environ.get("OPAGENT_ROOTS", ""))
    p.add_argument("--web-dist", default=os.environ.get("OPAGENT_WEB_DIST"))
    p.set_defaults(fn=cmd_dev)
    a = ap.parse_args(argv)
    a.fn(a)
