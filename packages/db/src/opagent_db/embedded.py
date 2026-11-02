"""Embedded PostgreSQL management for desktop / all-in-one (docs/08 §2.3, D4)."""

from __future__ import annotations

import glob
import os
import shlex
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path


def find_pg_bin() -> Path:
    env = os.environ.get("OPAGENT_PG_BIN")
    if env:
        return Path(env)
    pgc = shutil.which("pg_config")
    if pgc:
        out = subprocess.run([pgc, "--bindir"], text=True, capture_output=True, check=False).stdout.strip()
        if out and (Path(out) / "initdb").exists():
            return Path(out)
    for c in sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True):
        if (Path(c) / "initdb").exists():
            return Path(c)
    raise RuntimeError("PostgreSQL binaries not found; set OPAGENT_PG_BIN")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class PgAlreadyRunning(RuntimeError):
    """Another postgres already owns this data directory."""


class PgError(RuntimeError):
    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


def _pg_major(bin_dir: Path) -> int:
    out = subprocess.run([str(bin_dir / "initdb"), "--version"], text=True, capture_output=True, check=False).stdout
    digits = "".join(ch if ch.isdigit() or ch == "." else " " for ch in out).split()
    return int(digits[0].split(".")[0]) if digits else 0


def _is_postgres(pid: int) -> bool:
    """True when ``pid`` is alive and is a postgres server process (08 §2.3: never kill or unlink a live one)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # alive, owned by someone else
    except OSError:
        return False
    try:
        name = Path(f"/proc/{pid}/comm").read_text().strip()
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[0].decode(errors="replace")
    except OSError:  # no procfs (macOS / Windows): ask ps
        r = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], text=True, capture_output=True, check=False)
        name, cmd = r.stdout.strip(), ""
    return "postgres" in name or os.path.basename(cmd).startswith("postgres")


def shared_buffers_for(ram_gb: float) -> str:
    """Desktop profile (08 §2.3): a background helper must stay small on laptops."""
    return "128MB" if ram_gb <= 8 else "256MB"


def _total_ram_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (ValueError, OSError, AttributeError):  # pragma: no cover - non-POSIX
        return 8.0


@dataclass
class EmbeddedPg:
    """One app-owned cluster. ``desktop=True`` is the shipped profile: Unix socket only in a 0700 run dir
    (no TCP listener), UTF-8 C collation, a RAM-sized buffer pool and durable commits. The default
    (``desktop=False``) keeps a loopback TCP listener for dev and the test stack."""

    home: Path
    port: int = 0
    user: str = "opagent"
    db: str = "opagent"
    desktop: bool = False
    ram_gb: float | None = None

    def __post_init__(self) -> None:
        self.home = Path(self.home)
        self.bin = find_pg_bin()
        self.data = self.home / "data"
        self.run_dir = self.home / "run"
        self.prefix: list[str] = []
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            u = os.environ.get("OPAGENT_PG_OS_USER", "postgres")
            self.prefix = ["runuser", "-u", u, "--"]

    def run_as(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*self.prefix, *args], check=True, capture_output=True, text=True)

    def disk_free(self) -> int:
        return shutil.disk_usage(self.home).free

    def dsn_for(self, db: str) -> str:
        if self.desktop:
            return f"postgresql://{self.user}@/{db}?host={self.run_dir}&port={self.port}"
        return f"postgresql://{self.user}@127.0.0.1:{self.port}/{db}"

    @property
    def dsn(self) -> str:
        return self.dsn_for(self.db)

    def _own(self, path: Path) -> None:
        if self.prefix:
            shutil.chown(path, self.prefix[2], self.prefix[2])

    def _initdb(self) -> None:
        args = [str(self.bin / "initdb"), "-D", str(self.data), "-U", self.user, "-A", "trust", "-E", "UTF8"]
        if not self.desktop:
            args.append("--locale=C")
        elif _pg_major(self.bin) >= 17:  # byte-order collation that is identical on every OS (D4)
            args += ["--locale-provider=builtin", "--builtin-locale=C.UTF-8", "--locale=C.UTF-8"]
        else:
            args.append("--locale=C.UTF-8")
        self.run_as(*args)

    def _clear_stale_pid(self) -> None:
        pidfile = self.data / "postmaster.pid"
        if not pidfile.exists():
            return
        try:
            pid = int(pidfile.read_text().split()[0])
        except (OSError, ValueError, IndexError):
            pid = 0
        if pid > 0 and _is_postgres(pid):
            raise PgAlreadyRunning(f"postgres pid {pid} already owns {self.data}")
        pidfile.unlink(missing_ok=True)  # crash leftover (08 §2.3, DESK-009)

    def server_args(self) -> list[str]:
        if not self.desktop:
            return ["-p", str(self.port), "-k", str(self.home), "-c", "listen_addresses=127.0.0.1"]
        ram = self.ram_gb if self.ram_gb is not None else _total_ram_gb()
        return [
            "-p",
            str(self.port),
            "-k",
            str(self.run_dir),
            "-c",
            "listen_addresses=",
            "-c",
            "unix_socket_permissions=0700",
            "-c",
            f"shared_buffers={shared_buffers_for(ram)}",
            "-c",
            "fsync=on",
            "-c",
            "synchronous_commit=on",
        ]

    def prepare(self) -> list[str]:
        """Everything before the server runs — initdb once, run dir, stale pid — and the argv that starts it.
        The desktop shell executes this argv itself so it owns the postmaster PID (08 §1.1)."""
        self.home.mkdir(parents=True, exist_ok=True)
        self._own(self.home)
        try:
            if not (self.data / "PG_VERSION").exists():
                self._initdb()
        except subprocess.CalledProcessError as e:
            mapped = self._failed(e)
            if mapped is not None:
                raise mapped from e
            raise
        if self.desktop:
            self.run_dir.mkdir(mode=0o700, exist_ok=True)
            os.chmod(self.run_dir, 0o700)
            self._own(self.run_dir)
        self._clear_stale_pid()
        self.port = self.port or free_port()
        return [*self.prefix, str(self.bin / "postgres"), "-D", str(self.data), *self.server_args()]

    def _failed(self, err: subprocess.CalledProcessError) -> PgError | None:
        try:
            log = (self.home / "pg.log").read_text(errors="replace")
        except OSError:
            log = ""
        if "No space left on device" in log or "No space left on device" in (err.stderr or ""):
            return PgError("DISK_INSUFFICIENT_DB", free=self.disk_free())
        return None

    def start(self) -> str:
        self.prepare()
        try:
            self.run_as(
                str(self.bin / "pg_ctl"),
                "-D",
                str(self.data),
                "-o",
                shlex.join(self.server_args()),
                "-l",
                str(self.home / "pg.log"),
                "-w",
                "start",
            )
        except subprocess.CalledProcessError as e:
            mapped = self._failed(e)
            if mapped is not None:
                raise mapped from e  # the data directory is never touched on failure
            raise
        import psycopg

        with psycopg.connect(self.dsn_for("postgres"), autocommit=True) as c:
            if c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (self.db,)).fetchone() is None:
                c.execute(f'CREATE DATABASE "{self.db}"')
        return self.dsn

    def stop(self) -> None:
        subprocess.run(
            [*self.prefix, str(self.bin / "pg_ctl"), "-D", str(self.data), "-m", "fast", "stop"],
            check=False,
            capture_output=True,
        )
