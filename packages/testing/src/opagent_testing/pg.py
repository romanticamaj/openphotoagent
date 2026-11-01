"""embedded_pg: session-level initdb + per-test template clone (docs/09 §2.3, PG-013).

Binaries come from ``OPAGENT_PG_BIN``, ``pg_config --bindir`` or the distro
layout. When running as root (containers), PG is started as the ``postgres``
user. The cluster listens on 127.0.0.1 with a free port and trust auth.
"""

from __future__ import annotations

import glob
import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import psycopg


@dataclass
class PgCluster:
    host: str
    port: int
    user: str
    template: str
    data_dir: str = ""
    bin_dir: str = ""
    _prefix: list[str] = field(default_factory=list)

    def dsn(self, db: str) -> str:
        return f"postgresql://{self.user}@{self.host}:{self.port}/{db}"


@dataclass
class ClonedDb:
    dsn: str
    name: str
    clone_ms: float


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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run(cluster_prefix: list[str], args: list[str]) -> None:
    subprocess.run(cluster_prefix + args, check=True, capture_output=True, text=True)


def start_cluster(base_dir: str, init_schema: Callable[[str], None] | None = None) -> PgCluster:
    bin_dir = find_pg_bin()
    prefix: list[str] = []
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        user = os.environ.get("OPAGENT_PG_OS_USER", "postgres")
        prefix = ["runuser", "-u", user, "--"]
        shutil.chown(base_dir, user, user)
    data = str(Path(base_dir) / "data")
    _run(prefix, [str(bin_dir / "initdb"), "-D", data, "-U", "opagent", "-A", "trust", "--no-sync", "-E", "UTF8",
                  "--locale=C"])
    port = _free_port()
    opts = f"-p {port} -k {base_dir} -c listen_addresses=127.0.0.1 -c fsync=off -c synchronous_commit=off -c full_page_writes=off"
    _run(prefix, [str(bin_dir / "pg_ctl"), "-D", data, "-o", opts, "-l", str(Path(base_dir) / "pg.log"), "-w", "start"])
    cluster = PgCluster(host="127.0.0.1", port=port, user="opagent", template="opagent_tmpl", data_dir=data,
                        bin_dir=str(bin_dir), _prefix=prefix)
    with psycopg.connect(cluster.dsn("postgres"), autocommit=True) as c:
        c.execute("CREATE DATABASE opagent_tmpl")
    if init_schema is not None:
        init_schema(cluster.dsn("opagent_tmpl"))
    with psycopg.connect(cluster.dsn("postgres"), autocommit=True) as c:
        c.execute("ALTER DATABASE opagent_tmpl WITH IS_TEMPLATE true ALLOW_CONNECTIONS false")
    return cluster


def clone(cluster: PgCluster) -> ClonedDb:
    name = "t_" + uuid.uuid4().hex[:16]
    with psycopg.connect(cluster.dsn("postgres"), autocommit=True) as c:
        t0 = time.perf_counter()
        c.execute(f'CREATE DATABASE "{name}" TEMPLATE {cluster.template}')
        ms = (time.perf_counter() - t0) * 1000
    return ClonedDb(dsn=cluster.dsn(name), name=name, clone_ms=ms)


def drop(cluster: PgCluster, db: ClonedDb) -> None:
    with psycopg.connect(cluster.dsn("postgres"), autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{db.name}" WITH (FORCE)')


def stop_cluster(cluster: PgCluster) -> None:
    subprocess.run(cluster._prefix + [str(Path(cluster.bin_dir) / "pg_ctl"), "-D", cluster.data_dir, "-m", "immediate",
                                      "stop"], check=False, capture_output=True)
