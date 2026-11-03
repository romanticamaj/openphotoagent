"""Upgrading from every v0.x milestone to v1.0 (VERSIONING §4; 09b §9.4 MIG-001).

Each fixture is that milestone's real ``schema.py`` (``fixtures/compat/schema/``). A database is built with it, stamped
the way that release's ``migrate()`` left it (revision ``0001``), filled with a few rows, then upgraded by today's
``migrate()``: a backup is taken first, the result matches today's models exactly, and every row is still there.
"""

import importlib.util
from pathlib import Path

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from opagent_db import schema as head
from opagent_db.lifecycle import SCHEMA_REVISION, Backups, migrate, schema_revision
from opagent_testing.clock import FakeClock
from opagent_testing.pg import find_pg_bin
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]
MILESTONES = ["v0_1_d", "v0_2_a", "v0_2_b", "v0_2_c", "v0_3", "v0_4", "v0_5"]


def load(v):
    spec = importlib.util.spec_from_file_location(f"schema_{v}", ROOT / f"fixtures/compat/schema/schema_{v}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def with_db(dsn, name):
    from urllib.parse import urlsplit, urlunsplit

    u = urlsplit(dsn)
    return urlunsplit((u.scheme, u.netloc, "/" + name, u.query, u.fragment))


@pytest.fixture
def fresh_db(embedded_pg):
    made = []

    def make(tag):
        name = f"{embedded_pg.name}_{tag}"[:60]
        with psycopg.connect(with_db(embedded_pg.dsn, "postgres"), autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        made.append(name)
        return with_db(embedded_pg.dsn, name)

    yield make
    with psycopg.connect(with_db(embedded_pg.dsn, "postgres"), autocommit=True) as c:
        for name in made:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


SAMPLE = [
    "insert into workspace(id, name, settings) values ('default', 'Default', '{}')",
    'insert into worker(id, name, roots) values (\'w1\', \'local\', \'[{"alias": "photos", "mode": "ro"}]\')',
    "insert into source(id, workspace_id, worker_id, root_alias, include, exclude) values ('s1','default','w1','photos','[]','[]')",
    "insert into asset(id, workspace_id, content_hash, size_bytes, date_source, width, height, exif)"
    " values ('a1','default','h1',10,'exif',4000,3000,'{}')",
    "insert into asset_location(id, asset_id, source_id, rel_path, mtime, missing, size)"
    " values ('l1','a1','s1','2024/beach.jpg', now(), false, 10)",
    "insert into job(id, workspace_id, kind, status, params) values ('j1','default','organize','completed','{}')",
]


@pytest.mark.integration
@pytest.mark.tc("MIG-001")
@pytest.mark.parametrize("milestone", MILESTONES)
def test_mig_001_upgrade_from_each_milestone(fresh_db, milestone, tmp_path):
    dsn = fresh_db(milestone)
    load(milestone).create_all(dsn)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL PRIMARY KEY)")
        c.execute("INSERT INTO alembic_version VALUES ('0001')")  # what that release's migrate() left behind
        for q in SAMPLE:
            c.execute(q)
    bdir = tmp_path / "backups"
    bdir.mkdir()
    made = migrate(dsn, Backups(bdir, find_pg_bin(), FakeClock()))
    assert schema_revision(dsn) == SCHEMA_REVISION != "0001"
    assert made is not None and made.path.exists()  # a pre-migration dump before anything changed
    eng = create_engine(dsn.replace("postgresql://", "postgresql+psycopg://"))
    try:
        with eng.connect() as c:
            diff = compare_metadata(MigrationContext.configure(c), head.metadata)
            assert [d for d in diff if (d[0] if isinstance(d, tuple) else d[0][0]) != "remove_table"] == []
            assert c.execute(text("select rel_path from asset_location where id = 'l1'")).scalar() == "2024/beach.jpg"
            assert c.execute(text("select count(*) from asset")).scalar() == 1
            assert c.execute(text("select status from job where id = 'j1'")).scalar() == "completed"
            assert c.execute(text("select modality from embedding_space limit 1")).first() is None  # column exists
    finally:
        eng.dispose()
    assert migrate(dsn, Backups(bdir, find_pg_bin(), FakeClock())) is None  # nothing left to do


@pytest.mark.integration
@pytest.mark.tc("MIG-001")
def test_mig_001_fresh_install_and_dev_schema(fresh_db, tmp_path):
    from opagent_db.schema import create_all

    fresh = fresh_db("fresh")
    assert migrate(fresh, Backups(tmp_path, find_pg_bin(), FakeClock())) is None  # empty: no backup needed
    dev = fresh_db("dev")
    create_all(dev)  # dev and tests: tables straight from the models, never stamped
    migrate(dev, Backups(tmp_path, find_pg_bin(), FakeClock()))
    for dsn in (fresh, dev):
        eng = create_engine(dsn.replace("postgresql://", "postgresql+psycopg://"))
        with eng.connect() as c:
            assert compare_metadata(MigrationContext.configure(c), head.metadata) == []
        eng.dispose()
        assert schema_revision(dsn) == SCHEMA_REVISION
