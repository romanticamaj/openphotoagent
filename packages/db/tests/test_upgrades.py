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


FACE_ROWS = [
    "insert into workspace(id, name, settings) values ('default', 'Default', '{}')",
    "insert into asset(id, workspace_id, content_hash, size_bytes, date_source, width, height, exif) values"
    " ('a1','default','h1',10,'exif',4000,3000,'{}'), ('a2','default','h2',10,'exif',4000,3000,'{}')",
    "insert into person(id, workspace_id, origin, status, is_frozen, display_name, created_by) values"
    " ('named','default','cluster','named',true,'小明','user'), ('soft','default','cluster','unnamed',false,null,'system')",
    "create table bio.face_embedding_t partition of bio.face_embedding for values in ('default')",
    "insert into embedding_space(id, model_key, fingerprint, dim, modality) values (1,'fake','fake',2,'face')",
]


def _face(fid, asset, box, state="unassigned", person=None):
    return (
        "insert into face(id, workspace_id, asset_id, space_id, bbox, det_score, quality, quality_tier, state, person_id)"
        f" values ('{fid}','default','{asset}',1,ARRAY{box}::float[],0.9,'{{}}',3,'{state}',"
        f"{repr(person) if person else 'null'})"
    )


@pytest.mark.integration
@pytest.mark.tc("FACE-DET-011")
def test_face_det_011_copies_go_and_human_decisions_stay(fresh_db, tmp_path):
    """回歸: the installs that analysed a photo twice hold copies of its faces; grouping must start over without them."""
    from opagent_db.schema import create_all

    dsn = fresh_db("copies")
    create_all(dsn)
    box, other = [0.1, 0.1, 0.2, 0.2], [0.5, 0.5, 0.2, 0.2]
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL PRIMARY KEY)")
        c.execute("INSERT INTO alembic_version VALUES ('0002')")
        for q in FACE_ROWS:
            c.execute(q)
        c.execute(_face("f1", "a1", box))
        c.execute(_face("f2", "a1", box, "confirmed", "named"))  # a later copy, but the one a person decided on
        c.execute(_face("f3", "a1", box, "auto", "soft"))
        c.execute(_face("f4", "a1", other))  # someone else in the same photo
        c.execute(_face("f5", "a2", box))  # the same spot in another photo
        for fid in ("f1", "f2", "f3", "f4", "f5"):
            c.execute(
                "insert into bio.face_embedding(workspace_id, face_id, space_id, vec)"
                f" values ('default','{fid}',1,'[1,0]')"
            )
        c.execute(
            "insert into face_assignment(id, face_id, person_id, kind, method, actor)"
            " values ('r1','f1','soft','rejected','review','user')"
        )
        c.execute(
            "insert into face_cluster_run(id, workspace_id, space_id, algo, params, input_faces, clusters, noise)"
            " values ('run1','default',1,'hdbscan','{}',5,1,0)"
        )
    bdir = tmp_path / "backups"
    bdir.mkdir()
    migrate(dsn, Backups(bdir, find_pg_bin(), FakeClock()))
    with psycopg.connect(dsn) as c:
        assert [r[0] for r in c.execute("select id from face order by id")] == ["f2", "f4", "f5"]
        assert [r[0] for r in c.execute("select face_id from bio.face_embedding order by 1")] == ["f2", "f4", "f5"]
        assert c.execute("select state, person_id from face where id = 'f2'").fetchone() == ("confirmed", "named")
        # "this is not that person" outlives the copy it was said about
        assert c.execute("select face_id from face_assignment where id = 'r1'").fetchone() == ("f2",)
        assert c.execute("select count(*) from face_cluster_run").fetchone() == (
            0,
        )  # those groups were built on copies


def _result(rid, asset, raw):
    import json

    return (
        "insert into result(id, asset_id, task_config_hash, model_fingerprint, task_type, category, confidence, raw)"
        f" values ('{rid}','{asset}','h','fp','classify','other',0.2,'{json.dumps(raw)}'::jsonb)"
    )


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-018")
def test_inf_cas_018_guesses_left_by_a_vlm_timeout_are_removed(fresh_db, tmp_path):
    """回歸: installs that analysed while the GPU was busy hold the embedding's guess under the real cache key."""
    from opagent_db.schema import create_all

    dsn = fresh_db("timeouts")
    create_all(dsn)
    timed_out = {"provenance": {"error": "RUNTIME_TIMEOUT"}, "output": {"stages": {"route": "vlm", "vlm": None}}}
    answered = {"provenance": {"error": None}, "output": {"stages": {"route": "vlm", "vlm": {"category": "people"}}}}
    fast_path = {"provenance": {"error": None}, "output": {"stages": {"route": "accept", "vlm": None}}}
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL PRIMARY KEY)")
        c.execute("INSERT INTO alembic_version VALUES ('0003')")
        c.execute("insert into workspace(id, name, settings) values ('default', 'Default', '{}')")
        for a in ("a1", "a2", "a3"):
            c.execute(
                "insert into asset(id, workspace_id, content_hash, size_bytes, date_source, width, height, exif)"
                f" values ('{a}','default','h{a}',10,'exif',40,30,'{{}}')"
            )
        c.execute(_result("r1", "a1", timed_out))
        c.execute(_result("r2", "a2", answered))
        c.execute(_result("r3", "a3", fast_path))
        c.execute("insert into job(id, workspace_id, kind, status, params) values ('j1','default','label','completed','{}')")
        c.execute(
            "insert into job_item(id, job_id, asset_id, root_alias, rel, task, status, result_id)"
            " values ('i1','j1','a1','p','1.jpg','classify','done','r1'), ('i2','j1','a2','p','2.jpg','classify','done','r2')"
        )
        c.execute("insert into review(id, asset_id, job_id, result_id, reason) values ('v1','a1','j1','r1','low_confidence')")
    bdir = tmp_path / "backups"
    bdir.mkdir()
    migrate(dsn, Backups(bdir, find_pg_bin(), FakeClock()))
    with psycopg.connect(dsn) as c:
        assert [r[0] for r in c.execute("select id from result order by id")] == ["r2", "r3"]
        assert c.execute("select status, result_id, error_code from job_item where id = 'i1'").fetchone() == (
            "failed",
            None,
            "RUNTIME_TIMEOUT",
        )
        assert c.execute("select count(*) from review").fetchone() == (0,)
        assert c.execute("select count(*) from index_outbox where asset_id = 'a1'").fetchone() == (1,)

