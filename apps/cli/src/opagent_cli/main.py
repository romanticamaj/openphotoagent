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
    lan_hash = None
    if getattr(a, "lan", False):  # "allow LAN access" (08 §1.3): only ever with login enforced
        lan_hash = read_lan_password_hash(home)
        if lan_hash is None:
            print("LAN_PASSWORD_REQUIRED: set one with `opagent lan-password` first", file=sys.stderr, flush=True)
            raise SystemExit(5)
    bind_host = "0.0.0.0" if lan_hash else "127.0.0.1"  # noqa: S104  # nosec B104 - LAN mode, login enforced
    if not a.token_stdin and getattr(a, "host", None):  # container all-in-one: compose publishes to 127.0.0.1 only
        bind_host = a.host
    sock = _bind_loopback(a.port, bind_host)
    if sock is None:
        other = _probe(a.port)
        if other and other.get("app") == "openphotoagent" and other.get("data_fp") == fp:
            print(f"OpenPhotoAgent already runs on port {a.port} for this data folder", file=sys.stderr)
            raise SystemExit(3)  # the shell focuses that instance instead
        sock = _bind_loopback(0, bind_host)
        assert sock is not None
    port = int(sock.getsockname()[1])
    if a.token_stdin:
        from .layout import LayoutTooNew, ensure_layout

        try:
            ensure_layout(home)  # the data folder first, after a backup of what it touches (MIG-002)
        except LayoutTooNew as e:
            print(f"{e.code}: {e}", file=sys.stderr, flush=True)
            raise SystemExit(6) from None  # the shell shows the problem card with its fixes
        prepare_database(a.dsn, home)
    else:
        create_all(a.dsn)  # dev / tests: schema straight from the models
    # same machine: the API serves the Worker's derived thumbnail cache directly (10 §3.4)
    app = create_app(
        dsn=a.dsn,
        static_dir=a.web_dist,
        thumbs_dir=home / "worker" / "thumbs",
        launch_token=token,
        data_fp=fp,
        lan_password_hash=lan_hash,
        backups_dir=home / "backups" if a.token_stdin else None,  # Settings → Data on the desktop (08 §2.3)
    )
    # the socket is bound and listening before run/api.json names it (DESK-035): no check-then-bind race
    write_run_file(home, {"port": port, "pid": os.getpid(), "version": __version__, "data_fp": fp})
    if a.token_stdin:
        watch_parent_pipe()  # from here on, the shell disappearing stops us (start-up exits report their own codes)
    uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])


def cmd_controlplane(a: argparse.Namespace) -> None:
    """Self-hosted multi-user control plane (v0.5, 02 §1.1; deploy/compose/controlplane.yml).

    Configured from the environment: OPAGENT_DSN, OPAGENT_JWT_SECRET (≥ 32 chars), OPAGENT_PUBLIC_URL (the https
    address people and workers use), OPAGENT_OWNER_EMAIL / OPAGENT_OWNER_PASSWORD (first start only),
    OPAGENT_OIDC_ISSUER / _CLIENT_ID / _CLIENT_SECRET (optional). The CP never touches photos or GPUs."""
    import uvicorn
    from opagent_api.app import create_app

    env = os.environ
    dsn = a.dsn or env.get("OPAGENT_DSN") or ""
    secret = env.get("OPAGENT_JWT_SECRET") or ""
    if not dsn or len(secret) < 32:
        raise SystemExit("OPAGENT_DSN and OPAGENT_JWT_SECRET (at least 32 characters) are required")
    home = Path(a.home) if a.home else _home()
    home.mkdir(parents=True, exist_ok=True)
    from opagent_db.lifecycle import ensure_database

    ensure_database(dsn)
    if a.schema == "create-all":  # dev and tests
        from opagent_db.schema import create_all

        create_all(dsn)
    else:
        from opagent_core.clock import SystemClock
        from opagent_db.lifecycle import Backups, migrate

        pg_bin = Path(env.get("OPAGENT_PG_BIN", "/usr/lib/postgresql/17/bin"))
        migrate(dsn, Backups(home / "backups", pg_bin, SystemClock()))
    owner = None
    if env.get("OPAGENT_OWNER_EMAIL") and env.get("OPAGENT_OWNER_PASSWORD"):
        owner = (env["OPAGENT_OWNER_EMAIL"], env["OPAGENT_OWNER_PASSWORD"])
    oidc = None
    if env.get("OPAGENT_OIDC_ISSUER"):
        oidc = {
            "issuer": env["OPAGENT_OIDC_ISSUER"],
            "client_id": env.get("OPAGENT_OIDC_CLIENT_ID", ""),
            "client_secret": env.get("OPAGENT_OIDC_CLIENT_SECRET", ""),
        }
    app = create_app(
        dsn=dsn,
        static_dir=a.web_dist or env.get("OPAGENT_WEB_DIST"),
        thumbs_dir=home / "thumbs",
        multi_user=True,
        jwt_secret=secret,
        owner=owner,
        public_url=env.get("OPAGENT_PUBLIC_URL") or f"http://127.0.0.1:{a.port}",
        oidc=oidc,
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((a.host, a.port))
    sock.listen(128)
    write_run_file(home, {"port": int(sock.getsockname()[1]), "pid": os.getpid(), "version": __version__})
    uvicorn.Server(uvicorn.Config(app, log_level="warning", proxy_headers=True)).run(sockets=[sock])


from opagent_core.version import VERSION as __version__  # noqa: E402


def watch_parent_pipe() -> None:
    """Orphan protection where the OS has none (macOS, 08 §1.1): the shell keeps our stdin open for its whole
    life; EOF means it is gone, so stop — SIGTERM first for a graceful uvicorn shutdown, then hard exit."""

    def wait() -> None:
        try:
            while sys.stdin.buffer.read(4096):
                pass
        except (OSError, ValueError):
            pass
        os.kill(os.getpid(), signal.SIGTERM)
        threading.Timer(3.0, lambda: os._exit(0)).start()

    threading.Thread(target=wait, name="parent-pipe", daemon=True).start()


def prepare_database(dsn: str, home: Path) -> None:
    """Desktop start-up (08 §2.3): Alembic to head with a pg_dump first when anything is pending (PG-004), then
    the daily backup (PG-005). A database written by a newer app stops start-up with exit code 4 (PG-008)."""
    import json

    from opagent_core.clock import SystemClock
    from opagent_db.embedded import find_pg_bin
    from opagent_db.lifecycle import Backups, SchemaTooNew, ensure_database, migrate

    ensure_database(dsn, wait_s=60)  # the shell starts a bare postmaster; the app database is created on first start
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


def _bind_loopback(port: int, host: str = "127.0.0.1") -> socket.socket | None:
    """A listening socket — 127.0.0.1 only (DESK-014) unless LAN mode — or None when the port is taken."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
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


def lan_file(home: Path) -> Path:
    return Path(home) / "config" / "lan.json"


def read_lan_password_hash(home: Path) -> str | None:
    import json

    try:
        h = json.loads(lan_file(home).read_text(encoding="utf-8")).get("password_hash")
    except (OSError, ValueError):
        return None
    return h if isinstance(h, str) and h.startswith("$argon2id$") else None


def cmd_lan_password(a: argparse.Namespace) -> None:
    """Set the LAN-access password (read from stdin; stored only as an argon2id hash, file mode 0600)."""
    import json

    from opagent_api.security import hash_password

    pw = sys.stdin.readline().rstrip("\n")
    if len(pw) < 8:
        print("LAN_PASSWORD_TOO_SHORT: at least 8 characters", file=sys.stderr)
        raise SystemExit(2)
    home = Path(a.home) if a.home else _home()
    f = lan_file(home)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"password_hash": hash_password(pw)}, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, f)


def cmd_pg_prepare(a: argparse.Namespace) -> None:
    """For the desktop shell (08 §1.1, §2.3): initdb once, socket dir, stale pid check; print the postgres argv
    and DSNs as JSON. The shell runs the argv itself and supervises that PID."""
    import json

    from opagent_db.embedded import EmbeddedPg, PgAlreadyRunning, PgError

    home = Path(a.home) if a.home else _home() / "pg"
    pg = EmbeddedPg(home, desktop=True, ram_gb=a.ram_gb)
    try:
        argv = pg.prepare()
    except PgAlreadyRunning as e:
        print(json.dumps({"code": "PG_ALREADY_RUNNING", "detail": str(e)}), file=sys.stderr)
        raise SystemExit(3) from e
    except PgError as e:
        print(json.dumps({"code": e.code, "params": e.params}), file=sys.stderr)
        raise SystemExit(6) from e
    except subprocess.CalledProcessError as e:  # the shell shows this on its failure page
        print(f"{Path(e.cmd[0]).name} exited {e.returncode}: {e.stderr}", file=sys.stderr)
        raise SystemExit(6) from e
    result = {"argv": argv, "dsn": pg.dsn, "admin_dsn": pg.dsn_for("postgres"), "data": str(pg.data)}
    print(json.dumps({**result, "pg_ctl": str(pg.bin / "pg_ctl")}))


def _remote_home(a: argparse.Namespace) -> Path:
    return Path(a.home) if a.home else _home() / "remote-worker"


def cmd_worker_enroll(a: argparse.Namespace) -> None:
    """Pair this machine with a control plane (02 §5.2); the private key never leaves it."""
    import json

    from opagent_worker.remote import MODES, enroll

    home = _remote_home(a)
    cred = enroll(a.server, a.code, home, a.name)
    if a.egress_floor:
        if a.egress_floor not in MODES:
            raise SystemExit(f"--egress-floor must be one of {', '.join(MODES)}")
        (home / "egress-floor").write_text(a.egress_floor, encoding="utf-8")
    print(json.dumps({"worker_id": cred.worker_id, "server": cred.server}))


def _roots_file(home: Path) -> Path:
    return home / "roots.json"


def _read_roots_file(f: Path) -> list[dict[str, str]]:
    import json

    return list(json.loads(f.read_text(encoding="utf-8"))) if f.exists() else []


def _load_roots(home: Path) -> list[dict[str, str]]:
    return _read_roots_file(_roots_file(home))


def cmd_worker_roots(a: argparse.Namespace) -> None:
    """Photo folders this worker may touch. Only this local command changes them (C-09, invariant 2)."""
    import json
    import re

    home = _remote_home(a)
    home.mkdir(parents=True, exist_ok=True)
    roots = _load_roots(home)
    if a.action == "add":
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", a.alias or ""):
            raise SystemExit("alias: lower-case letters, digits, - and _")
        path = Path(a.path).expanduser().resolve()
        if not path.is_dir():
            raise SystemExit(f"not a folder: {path}")
        roots = [r for r in roots if r["alias"] != a.alias] + [{"alias": a.alias, "path": str(path), "mode": a.mode}]
    elif a.action == "remove":
        roots = [r for r in roots if r["alias"] != a.alias]
    if a.action != "list":
        _roots_file(home).write_text(json.dumps(roots, indent=2), encoding="utf-8")
    print(json.dumps(roots, indent=2))


def cmd_worker_run(a: argparse.Namespace) -> None:
    """Headless remote worker: outbound connections only, no UI, no listening port (MU-WORKER-001/006)."""
    from opagent_worker.agent import WorkerAgent
    from opagent_worker.remote import Channel, EgressFilter, RemoteClient

    home = _remote_home(a)
    floor_f = home / "egress-floor"
    floor = floor_f.read_text(encoding="utf-8").strip() if floor_f.exists() else None
    client = RemoteClient(home, egress=EgressFilter(floor=floor, log_path=home / "egress-denied.log"))
    backend = None
    if a.backend == "fake":
        from opagent_inference.fake import FakeBackend

        backend = FakeBackend()
    agent = WorkerAgent(
        client, roots=_load_roots(home), home=home, backend=backend, worker_id=client.worker_id, name=client.cred.name
    )
    agent.channel = Channel(client)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    agent.register()
    if agent.blocked_reason:
        print(f"control plane refused this worker: {agent.blocked_reason}", file=sys.stderr)
    if a.once:
        while agent.run_once(wait_s=0) or (backend is not None and agent.process_items_once()):
            pass
        return
    agent.run_forever(stop)


def cmd_worker(a: argparse.Namespace) -> None:
    import httpx

    if not a.api or not (a.roots or a.roots_file):
        raise SystemExit("opagent worker: --api and --roots are required (or use enroll / roots / run)")
    from opagent_worker.agent import WorkerAgent

    token = sys.stdin.readline().strip() if a.token_stdin else None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    client = httpx.Client(base_url=a.api, timeout=30, headers=headers)
    if a.token_stdin:
        watch_parent_pipe()
    backend = None
    if a.backend == "fake":  # tests and `opagent dev`; production is `auto`
        from opagent_inference.fake import FakeBackend

        backend = FakeBackend()
    models_root = Path(a.models_root) if a.models_root else None
    agent = WorkerAgent(
        client,
        roots=_read_roots_file(Path(a.roots_file)) if a.roots_file else _parse_roots(a.roots),
        home=Path(a.home) if a.home else _home() / "worker",
        backend=backend,
        worker_id=a.worker_id,
        models_root=models_root,
    )
    if a.backend == "auto":
        from opagent_worker.backends import load_local

        agent.backend_factory = lambda: load_local(agent.models_root)
    if a.token_stdin:  # desktop: pause heavy labelling on battery (06 §5, DESK-023)
        from opagent_worker.policy import ResourcePolicy
        from opagent_worker.power import system_power

        agent.policy = ResourcePolicy(power=system_power())
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.is_set():
        try:
            agent.refresh_backends()
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
        cmd_api(
            argparse.Namespace(dsn=dsn, port=a.port, web_dist=a.web_dist, token_stdin=False, home=None, host=a.host)
        )
    finally:
        worker.terminate()
        worker.wait(10)
        pg.stop()


def cmd_mcp(a: argparse.Namespace) -> None:
    """stdio MCP server for AI clients (05 §3.1): a thin relay to the running app; no DB, no photos, no worker."""
    from opagent_mcp.credstore import load

    from opagent_cli.mcp_stdio import Relay, serve

    home = Path(a.home) if a.home else _home()
    readonly = a.readonly or os.environ.get("OPAGENT_MCP_READONLY") == "1"
    serve(Relay(home, load(home, a.profile), server=a.server, readonly=readonly))


def cmd_login(a: argparse.Namespace) -> None:
    """Store a PAT made in the app's 「連接 AI 助手」 page in the OS keyring (MCP-PAT-001)."""
    from opagent_mcp.credstore import save

    home = Path(a.home) if a.home else _home()
    if a.token_stdin:
        token = sys.stdin.readline().strip()
    else:
        import getpass

        token = getpass.getpass("OpenPhotoAgent token (opagent_pat_…): ").strip()
    if not token.startswith("opagent_pat_"):
        sys.exit("not an OpenPhotoAgent token: it starts with opagent_pat_")
    r = save(token, home, a.profile)
    if r["store"] == "keyring":
        print("saved in the system keyring")
    else:
        print(f"no system keyring available: saved to {r['path']} (readable only by you)", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="opagent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("api")
    p.add_argument("--dsn", required=True)
    p.add_argument("--port", type=int, default=47800)
    p.add_argument("--web-dist")
    p.add_argument("--token-stdin", action="store_true", help="desktop: read the launch token from stdin")
    p.add_argument("--home", help="data directory (the shell passes it; default OPAGENT_HOME or ~/.opagent)")
    p.add_argument("--lan", action="store_true", help="allow LAN access (binds 0.0.0.0; password login enforced)")
    p.set_defaults(fn=cmd_api)
    p = sub.add_parser("controlplane", help="self-hosted multi-user control plane (Docker / NAS)")
    p.add_argument("--dsn")
    p.add_argument("--host", default="0.0.0.0")  # noqa: S104  # nosec B104 - the CP is a network service (behind TLS)
    p.add_argument("--port", type=int, default=47800)
    p.add_argument("--web-dist")
    p.add_argument("--home")
    p.add_argument("--schema", choices=["migrate", "create-all"], default="migrate")
    p.set_defaults(fn=cmd_controlplane)
    p = sub.add_parser("pg-prepare", help="desktop shell: prepare the embedded database, print its argv")
    p.add_argument("--home")
    p.add_argument("--ram-gb", type=float)
    p.set_defaults(fn=cmd_pg_prepare)
    p = sub.add_parser("lan-password", help="set the LAN-access password from stdin")
    p.add_argument("--home")
    p.set_defaults(fn=cmd_lan_password)
    p = sub.add_parser("worker", help="local worker (desktop) or, with a subcommand, a remote headless worker")
    wsub = p.add_subparsers(dest="worker_cmd")
    q = wsub.add_parser("enroll", help="pair with a control plane using a one-time code")
    q.add_argument("--server", required=True)
    q.add_argument("--code", required=True)
    q.add_argument("--name", default=socket.gethostname()[:100])
    q.add_argument("--egress-floor", help="never send more than this policy allows, whatever the CP says")
    q.add_argument("--home")
    q.set_defaults(fn=cmd_worker_enroll)
    q = wsub.add_parser("roots", help="the photo folders this worker may use (only settable here)")
    q.add_argument("action", choices=["add", "remove", "list"])
    q.add_argument("alias", nargs="?")
    q.add_argument("path", nargs="?")
    q.add_argument("--mode", choices=["ro", "rw"], default="ro")
    q.add_argument("--home")
    q.set_defaults(fn=cmd_worker_roots)
    q = wsub.add_parser("run", help="run as a headless remote worker")
    q.add_argument("--headless", action="store_true", help="no UI, no API port (the default for remote workers)")
    q.add_argument("--backend", default="none", choices=["fake", "none"])
    q.add_argument("--once", action="store_true")
    q.add_argument("--home")
    q.set_defaults(fn=cmd_worker_run)
    p.add_argument("--api")
    p.add_argument("--roots", help="alias=path:mode,...")
    p.add_argument("--worker-id")
    p.add_argument("--roots-file", help="desktop: the JSON list the shell writes after the folder dialog")
    p.add_argument("--backend", default="fake", choices=["fake", "none", "auto"])
    p.add_argument("--models-root", help="where models live (default <home>/models)")
    p.add_argument("--once", action="store_true", help="process what is queued, then exit")
    p.add_argument("--home", help="worker state directory (default <OPAGENT_HOME>/worker)")
    p.add_argument("--token-stdin", action="store_true", help="desktop: read the launch token from stdin")
    p.set_defaults(fn=cmd_worker)
    p = sub.add_parser("mcp", help="MCP server over stdio for AI assistants (Claude Desktop, Claude Code, Cursor)")
    p.add_argument("--server", help="app address (default: read <data dir>/run/api.json)")
    p.add_argument("--readonly", action="store_true", help="only offer read-only tools")
    p.add_argument("--profile", default="default")
    p.add_argument("--home")
    p.set_defaults(fn=cmd_mcp)
    p = sub.add_parser("login", help="store an OpenPhotoAgent access token in the system keyring")
    p.add_argument("--token-stdin", action="store_true")
    p.add_argument("--profile", default="default")
    p.add_argument("--home")
    p.set_defaults(fn=cmd_login)
    p = sub.add_parser("dev")
    p.add_argument("--port", type=int, default=47800)
    p.add_argument("--roots", default=os.environ.get("OPAGENT_ROOTS", ""))
    p.add_argument("--web-dist", default=os.environ.get("OPAGENT_WEB_DIST"))
    p.add_argument("--host", default=os.environ.get("OPAGENT_BIND_HOST"), help="container only: bind address")
    p.set_defaults(fn=cmd_dev)
    a = ap.parse_args(argv)
    a.fn(a)
