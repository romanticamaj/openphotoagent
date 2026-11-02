"""Schema migrations and backups for the app-owned database (docs/08 §2.3, §6; VERSIONING §4).

* ``migrate`` runs Alembic to head. When anything is pending on a non-empty database it first takes a
  ``pg_dump -Fc`` backup (PG-004). A database stamped by a newer app is refused (PG-008): migrations are
  forward-only and the fix is "restore a backup" or "install the newer version".
* ``Backups`` keeps one daily dump for the last 7 days plus every pre-migration dump (PG-005) and can
  restore any of them (PG-006). Files are written under a temporary name and renamed when complete.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psycopg
import psycopg.conninfo
import psycopg.sql
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from opagent_core.clock import Clock

from .schema import engine_for

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
DAILY_KEEP = 7
_MIGRATIONS = Path(__file__).with_name("migrations")


def _config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    return cfg


SCHEMA_REVISION: str = ScriptDirectory.from_config(_config()).get_current_head() or ""


class SchemaTooNew(RuntimeError):
    code = "DB_SCHEMA_TOO_NEW"

    def __init__(self, found: str) -> None:
        super().__init__(f"database revision {found} is newer than this app ({SCHEMA_REVISION})")
        self.params = {"found": found}
        self.hint = "restore a backup made by this version, or install the newer version"


def _default_runner(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), check=True, capture_output=True, text=True)


@dataclass(frozen=True)
class Backup:
    kind: str  # "daily" | "pre-migration"
    path: Path
    at: str  # sortable UTC stamp


_NAME = re.compile(r"^(daily|pre-migration)-(\d{8}T\d{6}\d{6})\.dump$")


class Backups:
    def __init__(self, directory: Path, pg_bin: Path, clock: Clock, runner: Runner | None = None) -> None:
        self.dir = Path(directory)
        self.pg_bin = Path(pg_bin)
        self.clock = clock
        self.run = runner or _default_runner

    def _stamp(self) -> str:
        return self.clock.now().strftime("%Y%m%dT%H%M%S%f")

    def list(self) -> list[Backup]:
        if not self.dir.exists():
            return []
        out = []
        for p in self.dir.iterdir():
            m = _NAME.match(p.name)
            if m:
                out.append(Backup(m.group(1), p, m.group(2)))
        return sorted(out, key=lambda b: (b.at, b.kind))

    def _dump(self, dsn: str, kind: str) -> Backup:
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = self._stamp()
        final = self.dir / f"{kind}-{stamp}.dump"
        tmp = self.dir / f".{final.name}.partial"
        self.run(str(self.pg_bin / "pg_dump"), "-Fc", "-f", str(tmp), "-d", dsn)
        tmp.replace(final)
        return Backup(kind, final, stamp)

    def pre_migration(self, dsn: str) -> Backup:
        return self._dump(dsn, "pre-migration")

    def daily(self, dsn: str) -> Backup | None:
        today = self.clock.now().strftime("%Y%m%d")
        dailies = [b for b in self.list() if b.kind == "daily"]
        if any(b.at.startswith(today) for b in dailies):
            return None
        made = self._dump(dsn, "daily")
        dailies = [b for b in self.list() if b.kind == "daily"]
        for old in dailies[:-DAILY_KEEP]:
            old.path.unlink(missing_ok=True)
        return made

    def restore(self, dsn: str, item: Backup) -> None:
        self.run(
            str(self.pg_bin / "pg_restore"),
            "--clean",
            "--if-exists",
            "--single-transaction",
            "--no-owner",
            "-d",
            dsn,
            str(item.path),
        )


def ensure_database(dsn: str) -> None:
    """Create the app database named in ``dsn`` when missing (connecting through the ``postgres`` database)."""
    info = psycopg.conninfo.conninfo_to_dict(dsn)
    name = str(info.get("dbname") or "")
    admin = psycopg.conninfo.make_conninfo(dsn, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as c:
        if c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is None:
            c.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))


def schema_revision(dsn: str) -> str | None:
    eng = engine_for(dsn)
    try:
        with eng.connect() as c:
            return MigrationContext.configure(c).get_current_revision()
    finally:
        eng.dispose()


def _has_user_tables(dsn: str) -> bool:
    with psycopg.connect(dsn) as c:
        row = c.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
            " AND table_name <> 'alembic_version'"
        ).fetchone()
    return bool(row and row[0])


def migrate(dsn: str, backups: Backups) -> Backup | None:
    """Bring the database to ``SCHEMA_REVISION``. Returns the pre-migration backup when one was taken."""
    script = ScriptDirectory.from_config(_config())
    current = schema_revision(dsn)
    if current is not None:
        try:
            script.get_revision(current)
        except CommandError as e:  # unknown revision → written by a newer app
            raise SchemaTooNew(current) from e
    if current == SCHEMA_REVISION:
        return None
    made = backups.pre_migration(dsn) if _has_user_tables(dsn) else None
    eng = engine_for(dsn)
    try:
        with eng.begin() as conn:
            cfg = _config()
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
    finally:
        eng.dispose()
    return made


__all__ = ["SCHEMA_REVISION", "Backup", "Backups", "SchemaTooNew", "migrate", "schema_revision"]
