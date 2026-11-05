"""Embedded PostgreSQL management for desktop / all-in-one (docs/08 §2.3, D4)."""

from __future__ import annotations

import glob
import os
import secrets
import shlex
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


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


def write_private(path: Path, text: str) -> None:
    """A file only its owner can read (0600 on POSIX)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def _port_is_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


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


def _windows_image_name(pid: int) -> str | None:
    """The executable name of a live process; never signals it (``os.kill(pid, 0)`` is CTRL_C_EVENT on Windows)."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        size = ctypes.c_ulong(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size)):
            return None
        return Path(buf.value).name
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _is_postgres(pid: int) -> bool:
    """True when ``pid`` is alive and is a postgres server process (08 §2.3: never kill or unlink a live one)."""
    if os.name == "nt":
        return (_windows_image_name(pid) or "").lower() == "postgres.exe"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # alive, owned by someone else
    except OSError:
        return False
    try:
        name = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
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
    transport: str = ""  # desktop only: "socket" (macOS, Linux) or "tcp" (Windows), 08 §1.3

    def __post_init__(self) -> None:
        self.home = Path(self.home)
        self.bin = find_pg_bin()
        self.data = self.home / "data"
        self.run_dir = self.home / "run"
        self.transport = self.transport or ("tcp" if os.name == "nt" else "socket")
        self.passfile = self.home / "pgpass"
        self.prefix: list[str] = []
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            u = os.environ.get("OPAGENT_PG_OS_USER", "postgres")
            self.prefix = ["runuser", "-u", u, "--"]

    def run_as(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Output goes through files, not pipes: on Windows the postmaster inherits pg_ctl's stdout, so reading a pipe
        never reaches EOF and ``pg_ctl start`` hung for good."""
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            r = subprocess.run([*self.prefix, *args], check=False, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
            out.seek(0)
            err.seek(0)
            stdout, stderr = (f.read().decode("utf-8", "replace") for f in (out, err))
        if r.returncode:
            raise subprocess.CalledProcessError(r.returncode, r.args, stdout, stderr)
        return subprocess.CompletedProcess(r.args, 0, stdout, stderr)

    def disk_free(self) -> int:
        return shutil.disk_usage(self.home).free

    def dsn_for(self, db: str) -> str:
        if self.desktop and self.transport == "tcp":  # the password stays in the passfile, off the shell's argv
            return f"postgresql://{self.user}@127.0.0.1:{self.port}/{db}?passfile={quote(str(self.passfile), safe='')}"
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
        scram = self.desktop and self.transport == "tcp"
        auth = "scram-sha-256" if scram else "trust"
        args = [str(self.bin / "initdb"), "-D", str(self.data), "-U", self.user, "-A", auth, "-E", "UTF8"]
        if not self.desktop:
            args.append("--locale=C")
        elif _pg_major(self.bin) >= 17:  # byte-order collation that is identical on every OS (D4)
            # collation/ctype come from the builtin provider; the OS-level lc_* must be "C", the only locale every
            # OS has (C.UTF-8 is Linux-only — initdb refuses it on macOS and Windows, PG-016)
            args += ["--locale-provider=builtin", "--builtin-locale=C.UTF-8", "--locale=C"]
        else:
            args.append("--locale=C.UTF-8")
        if not scram:
            self.run_as(*args)
            return
        self.home.mkdir(parents=True, exist_ok=True)
        password = secrets.token_urlsafe(32)
        once = self.home / ".pw-initdb"
        write_private(once, password)
        try:
            self.run_as(*args, f"--pwfile={once}")
        finally:
            once.unlink(missing_ok=True)
        write_private(self.passfile, f"127.0.0.1:*:*:{self.user}:{password}\n")

    def _clear_stale_pid(self) -> None:
        pidfile = self.data / "postmaster.pid"
        if not pidfile.exists():
            return
        try:
            pid = int(pidfile.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            pid = 0
        if pid > 0 and _is_postgres(pid):
            raise PgAlreadyRunning(f"postgres pid {pid} already owns {self.data}")
        pidfile.unlink(missing_ok=True)  # crash leftover (08 §2.3, DESK-009)

    def server_args(self) -> list[str]:
        if not self.desktop:
            return ["-p", str(self.port), "-k", str(self.home), "-c", "listen_addresses=127.0.0.1"]
        ram = self.ram_gb if self.ram_gb is not None else _total_ram_gb()
        if self.transport == "tcp":
            listen = ["-c", "listen_addresses=127.0.0.1"]
        else:
            listen = ["-k", str(self.run_dir), "-c", "listen_addresses=", "-c", "unix_socket_permissions=0700"]
        return [
            "-p",
            str(self.port),
            *listen,
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
        self.port = self.port or self._stable_port()
        return [*self.prefix, str(self.bin / "postgres"), "-D", str(self.data), *self.server_args()]

    def _stable_port(self) -> int:
        """The api keeps its DSN while the shell restarts a crashed postmaster, so the port outlives the process."""
        if not self.desktop:
            return free_port()
        saved = self.home / "port"
        try:
            port = int(saved.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            port = 0
        if not port or not _port_is_free(port):
            port = free_port()
            saved.write_text(str(port), encoding="utf-8")
        return port

    def _failed(self, err: subprocess.CalledProcessError) -> PgError | None:
        try:
            log = (self.home / "pg.log").read_text(errors="replace", encoding="utf-8")
        except OSError:
            log = ""
        if "No space left on device" in log or "No space left on device" in (err.stderr or ""):
            return PgError("DISK_INSUFFICIENT_DB", free=self.disk_free())
        return None

    def _pg_ctl_start(self) -> None:
        log = str(self.home / "pg.log")
        self.run_as(
            str(self.bin / "pg_ctl"),
            "-D",
            str(self.data),
            "-o",
            shlex.join(self.server_args()),
            "-l",
            log,
            "-w",
            "start",
        )

    def start(self) -> str:
        self.prepare()
        try:
            self._pg_ctl_start()
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
