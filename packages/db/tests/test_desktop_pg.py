"""Embedded PostgreSQL for the desktop (08 §2.3, D4): initdb, profile, socket-only, stale pid, backups, migrations."""

import os
import shutil
import stat
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

import psycopg
import psycopg.conninfo
import pytest
from opagent_db.embedded import EmbeddedPg, PgAlreadyRunning, PgError
from opagent_db.lifecycle import SCHEMA_REVISION, Backups, SchemaTooNew, migrate, schema_revision
from opagent_testing.clock import FakeClock


@pytest.fixture
def base():
    # pytest's tmp_path is private to root; the server runs as the postgres user when the runner is root
    d = tempfile.mkdtemp(prefix="opagent-dpg-")
    os.chmod(d, 0o755)
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def pg(base):
    started = []

    def make(name="d", **kw):
        p = EmbeddedPg(base / name, desktop=True, **kw)
        started.append(p)
        return p

    yield make
    for p in started:
        p.stop()


def backups_dir(base):
    d = base / "backups"
    d.mkdir(mode=0o777, exist_ok=True)
    os.chmod(d, 0o777)
    return d


def q(dsn, sql):
    with psycopg.connect(dsn, autocommit=True) as c:
        return c.execute(sql).fetchone()[0]


@pytest.mark.integration
@pytest.mark.tc("PG-001", "PG-012")
def test_pg_001_012_initdb_once_utf8_socket_only(pg):
    p = pg()
    dsn = p.start()
    assert q(dsn, "SHOW server_encoding") == "UTF8"
    assert q(dsn, "SELECT datcollate FROM pg_database WHERE datname = current_database()").lower().replace("-", "") in (
        "c.utf8",
    )
    assert q(dsn, "SHOW listen_addresses") == ""  # Unix socket only (mac/linux)
    run = p.home / "run"
    assert stat.S_IMODE(run.stat().st_mode) == 0o700
    ver = (p.data / "PG_VERSION").stat().st_mtime_ns
    p.stop()
    p.start()  # a second start never re-runs initdb
    assert (p.data / "PG_VERSION").stat().st_mtime_ns == ver


@pytest.mark.integration
@pytest.mark.tc("PG-003")
@pytest.mark.parametrize("ram,buffers", [(8, "128MB"), (32, "256MB")])
def test_pg_003_desktop_profile(pg, ram, buffers):
    dsn = pg(f"r{ram}", ram_gb=ram).start()
    assert q(dsn, "SHOW shared_buffers") == buffers
    assert q(dsn, "SHOW fsync") == "on" and q(dsn, "SHOW synchronous_commit") == "on"


@pytest.mark.integration
@pytest.mark.tc("PG-002")
def test_pg_002_pgvector_hnsw(pg):
    dsn = pg().start()
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        c.execute("CREATE TABLE v (id int, e vector(3))")
        c.execute("INSERT INTO v VALUES (1, '[1,0,0]'), (2, '[0,1,0]'), (3, '[0.9,0.1,0]')")
        c.execute("CREATE INDEX ON v USING hnsw (e vector_cosine_ops)")
        rows = c.execute("SELECT id FROM v ORDER BY e <=> '[1,0,0]' LIMIT 2").fetchall()
    assert [r[0] for r in rows] == [1, 3]


@pytest.mark.integration
@pytest.mark.tc("DESK-009")
def test_desk_009_stale_postmaster_pid(pg):
    p = pg()
    p.start()
    p.stop()
    pidfile = p.data / "postmaster.pid"
    # a pid that no longer exists → cleaned up, start succeeds
    pidfile.write_text("999999\n" + str(p.data) + "\n")
    p.start()
    p.stop()
    # a live process that is not postgres (this test runner) → also cleaned up
    pidfile.write_text(f"{os.getpid()}\n{p.data}\n")
    p.start()
    live_pid = int(pidfile.read_text().split()[0])
    # a live postgres → never removed; the caller is told another instance owns the data
    other = EmbeddedPg(p.home, desktop=True)
    with pytest.raises(PgAlreadyRunning):
        other.start()
    assert int(pidfile.read_text().split()[0]) == live_pid


@pytest.mark.integration
@pytest.mark.tc("PG-004", "PG-008")
def test_pg_004_008_backup_before_migration_and_no_downgrade(pg, base):
    p = pg()
    dsn = p.start()
    bk = Backups(backups_dir(base), p.bin, FakeClock(), runner=p.run_as)
    migrate(dsn, bk)  # fresh database: schema created, no backup needed
    assert schema_revision(dsn) == SCHEMA_REVISION and bk.list() == []
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("DELETE FROM alembic_version")  # back at base: the head migration is pending
    migrate(dsn, bk)
    assert [b.kind for b in bk.list()] == ["pre-migration"]  # dumped before upgrading
    assert schema_revision(dsn) == SCHEMA_REVISION
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("UPDATE alembic_version SET version_num = 'ffff_written_by_a_newer_app'")
    with pytest.raises(SchemaTooNew) as e:
        migrate(dsn, bk)
    assert e.value.code == "DB_SCHEMA_TOO_NEW" and "restore" in e.value.hint


@pytest.mark.integration
@pytest.mark.tc("PG-005", "PG-006")
def test_pg_005_006_daily_retention_and_restore(pg, base):
    p = pg()
    dsn = p.start()
    clk = FakeClock()
    bk = Backups(backups_dir(base), p.bin, clk, runner=p.run_as)
    migrate(dsn, bk)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("INSERT INTO workspace(id, name) VALUES ('default', 'd') ON CONFLICT DO NOTHING")
    bk.pre_migration(dsn)
    for _ in range(10):
        bk.daily(dsn)
        bk.daily(dsn)  # twice the same day: still one
        clk.advance(timedelta(days=1).total_seconds())
    kinds = [b.kind for b in bk.list()]
    assert kinds.count("daily") == 7 and kinds.count("pre-migration") == 1
    snap = bk.list()[-1]
    n0 = q(dsn, "SELECT count(*) FROM workspace")
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("INSERT INTO workspace(id, name) VALUES ('extra', 'x')")
    bk.restore(dsn, snap)
    assert q(dsn, "SELECT count(*) FROM workspace") == n0


@pytest.mark.unit
@pytest.mark.tc("PG-009")
def test_pg_009_full_disk_is_reported_and_data_kept(tmp_path):
    p = EmbeddedPg(tmp_path / "d", desktop=True)
    p.data.mkdir(parents=True)
    (p.data / "PG_VERSION").write_text("16\n")
    (p.data / "base").mkdir()

    def runner(*args: str) -> subprocess.CompletedProcess[str]:
        if "start" in args:
            (p.home / "pg.log").write_text("FATAL:  could not write lock file: No space left on device\n")
            raise subprocess.CalledProcessError(1, list(args))
        return subprocess.CompletedProcess(list(args), 0, "", "")

    p.run_as = runner  # type: ignore[method-assign]
    p.disk_free = lambda: 12_345  # type: ignore[method-assign]
    with pytest.raises(PgError) as e:
        p.start()
    assert e.value.code == "DISK_INSUFFICIENT_DB" and e.value.params["free"] == 12_345
    assert (p.data / "PG_VERSION").exists() and (p.data / "base").exists()  # never delete the data dir


@pytest.mark.integration
@pytest.mark.tc("PG-012")
def test_pg_012_shell_runs_postgres_directly_from_prepared_argv(base):
    """08 §1.1: the Rust shell owns the postgres PID (no pg_ctl); the profile still comes from EmbeddedPg."""
    import json
    import sys
    import time

    home = base / "shell"
    r = subprocess.run(
        [sys.executable, "-I", "-m", "opagent_cli", "pg-prepare", "--home", str(home), "--ram-gb", "8"],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    info = json.loads(r.stdout)
    exe = [i for i, a in enumerate(info["argv"]) if Path(a).name == "postgres"]
    assert exe and info["argv"][exe[-1] + 1] == "-D"  # the server binary itself, not a wrapper
    assert "pg_ctl" not in " ".join(info["argv"])
    proc = subprocess.Popen(info["argv"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        t0 = time.monotonic()  # noqa: TID251 - waiting on a real server process
        while True:
            try:
                assert q(info["admin_dsn"], "SHOW listen_addresses") == ""
                assert q(info["admin_dsn"], "SHOW shared_buffers") == "128MB"
                break
            except psycopg.OperationalError:
                assert time.monotonic() - t0 < 30, "postgres never became ready"  # noqa: TID251
                time.sleep(0.2)  # noqa: TID251
    finally:
        proc.terminate()  # SIGTERM = smart shutdown of the postmaster we own
        proc.wait(30)


@pytest.mark.integration
@pytest.mark.tc("PG-001")
def test_pg_001_app_database_created_on_first_start(embedded_pg):
    from opagent_db.lifecycle import ensure_database

    dsn = psycopg.conninfo.make_conninfo(embedded_pg.dsn, dbname=f"fresh_{os.getpid()}")
    ensure_database(dsn)
    ensure_database(dsn)  # idempotent on every later start
    assert q(dsn, "SELECT current_database()") == f"fresh_{os.getpid()}"
    with psycopg.connect(embedded_pg.dsn, autocommit=True) as c:
        c.execute(f'DROP DATABASE "fresh_{os.getpid()}"')
