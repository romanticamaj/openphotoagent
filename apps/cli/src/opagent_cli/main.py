"""`opagent` CLI (CLAUDE.md §3: CLI name opagent, env prefix OPAGENT_)."""

from __future__ import annotations

import argparse
import os
import signal
import socket
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
    """API process. Desktop mode (``--token-stdin``): the shell writes the launch token on stdin — never argv or the
    environment (DESK-012); binds 127.0.0.1 only (DESK-014); resolves port conflicts and records the actual port in
    ``<home>/run/api.json`` (0600) for the tray, the browser handoff and ``opagent mcp`` (DESK-010)."""
    import uvicorn
    from opagent_api.app import create_app
    from opagent_db.schema import create_all

    token = sys.stdin.readline().strip() if a.token_stdin else None
    home = Path(a.home) if a.home else _home()
    from .logs import setup_logging

    setup_logging(home / "logs", "api", token=token)
    fp = data_fingerprint(home)
    sock = _bind_loopback(a.port)
    if sock is None:
        other = _probe(a.port)
        if other and other.get("app") == "openphotoagent" and other.get("data_fp") == fp:
            print(f"OpenPhotoAgent already runs on port {a.port} for this data folder", file=sys.stderr)
            raise SystemExit(3)  # the shell focuses that instance instead
        sock = _bind_loopback(0)
        assert sock is not None
    port = int(sock.getsockname()[1])
    if a.token_stdin:
        prepare_database(a.dsn, home)
    else:
        create_all(a.dsn)  # dev / tests: schema straight from the models
    # same machine: the API serves the Worker's derived thumbnail cache directly (10 §3.4)
    app = create_app(
        dsn=a.dsn, static_dir=a.web_dist, thumbs_dir=home / "worker" / "thumbs", launch_token=token, data_fp=fp
    )
    # the socket is bound and listening before run/api.json names it (DESK-035): no check-then-bind race
    write_run_file(home, {"port": port, "pid": os.getpid(), "version": __version__, "data_fp": fp})
    uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])


from opagent_core.version import VERSION as __version__  # noqa: E402


def prepare_database(dsn: str, home: Path) -> None:
    """Desktop start-up (08 §2.3): Alembic to head with a pg_dump first when anything is pending (PG-004), then
    the daily backup (PG-005). A database written by a newer app stops start-up with exit code 4 (PG-008)."""
    import json

    from opagent_core.clock import SystemClock
    from opagent_db.embedded import find_pg_bin
    from opagent_db.lifecycle import Backups, SchemaTooNew, migrate

    backups = Backups(home / "backups", find_pg_bin(), SystemClock())
    try:
        migrate(dsn, backups)
    except SchemaTooNew as e:
        print(json.dumps({"code": e.code, "params": e.params, "hint": e.hint}), file=sys.stderr, flush=True)
        raise SystemExit(4) from e
    backups.daily(dsn)


def data_fingerprint(home: Path) -> str:
    import hashlib

    return hashlib.sha256(str(Path(home).resolve()).encode()).hexdigest()[:16]


def _bind_loopback(port: int) -> socket.socket | None:
    """A listening socket on 127.0.0.1 only (DESK-014), or None when the port is taken."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(2048)
    except OSError:
        s.close()
        return None
    s.set_inheritable(True)
    return s


def _probe(port: int) -> dict[str, str] | None:
    import httpx

    try:
        r = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1.0)
        return dict(r.json()) if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def write_run_file(home: Path, info: dict[str, object]) -> None:
    """Atomic, owner-only (0600) — it names the port of a token-protected API."""
    import json

    run = Path(home) / "run"
    run.mkdir(parents=True, exist_ok=True)
    tmp = run / "api.json.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(info, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, run / "api.json")


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
        cmd_api(argparse.Namespace(dsn=dsn, port=a.port, web_dist=a.web_dist, token_stdin=False, home=None))
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
    p.add_argument("--token-stdin", action="store_true", help="desktop: read the launch token from stdin")
    p.add_argument("--home", help="data directory (the shell passes it; default OPAGENT_HOME or ~/.opagent)")
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
