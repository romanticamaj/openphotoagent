import psycopg
import pytest


@pytest.mark.integration
@pytest.mark.tc("PG-013")
def test_pg_013_template_clone_is_fast_and_isolated(embedded_pg, embedded_pg_cluster):
    # each test gets its own clone of the migrated template database
    assert embedded_pg.clone_ms < 200, f"template clone took {embedded_pg.clone_ms:.1f} ms"
    with psycopg.connect(embedded_pg.dsn, autocommit=True) as c:
        c.execute("CREATE TABLE pg013_probe(x int)")
        c.execute("INSERT INTO pg013_probe VALUES (1)")
    # the template is untouched: a second clone does not see the probe table
    from opagent_testing.pg import clone, drop

    other = clone(embedded_pg_cluster)
    try:
        with psycopg.connect(other.dsn) as c:
            n = c.execute("SELECT count(*) FROM pg_tables WHERE tablename='pg013_probe'").fetchone()[0]
        assert n == 0
    finally:
        drop(embedded_pg_cluster, other)
