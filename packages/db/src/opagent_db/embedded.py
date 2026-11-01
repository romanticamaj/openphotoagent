"""Embedded PostgreSQL management for desktop / all-in-one (docs/08 §2.3, D4)."""

from __future__ import annotations

import glob
import os
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


@dataclass
class EmbeddedPg:
    home: Path
    port: int = 0
    user: str = "opagent"
    db: str = "opagent"

    def __post_init__(self) -> None:
        self.home = Path(self.home)
        self.bin = find_pg_bin()
        self.data = self.home / "data"
        self.prefix: list[str] = []
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            u = os.environ.get("OPAGENT_PG_OS_USER", "postgres")
            self.prefix = ["runuser", "-u", u, "--"]

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*self.prefix, *args], check=True, capture_output=True, text=True)

    @property
    def dsn(self) -> str:
        return f"postgresql://{self.user}@127.0.0.1:{self.port}/{self.db}"

    def start(self) -> str:
        self.home.mkdir(parents=True, exist_ok=True)
        if self.prefix:
            shutil.chown(self.home, self.prefix[2], self.prefix[2])
        if not (self.data / "PG_VERSION").exists():
            self._run(
                str(self.bin / "initdb"),
                "-D",
                str(self.data),
                "-U",
                self.user,
                "-A",
                "trust",
                "-E",
                "UTF8",
                "--locale=C",
            )
        self.port = self.port or free_port()
        opts = f"-p {self.port} -k {self.home} -c listen_addresses=127.0.0.1"
        self._run(
            str(self.bin / "pg_ctl"), "-D", str(self.data), "-o", opts, "-l", str(self.home / "pg.log"), "-w", "start"
        )
        import psycopg

        with psycopg.connect(f"postgresql://{self.user}@127.0.0.1:{self.port}/postgres", autocommit=True) as c:
            if c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (self.db,)).fetchone() is None:
                c.execute(f'CREATE DATABASE "{self.db}"')
        return self.dsn

    def stop(self) -> None:
        subprocess.run(
            [*self.prefix, str(self.bin / "pg_ctl"), "-D", str(self.data), "-m", "fast", "stop"],
            check=False,
            capture_output=True,
        )
