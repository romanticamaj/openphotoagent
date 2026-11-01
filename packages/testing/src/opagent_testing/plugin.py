"""Global pytest fixtures (docs/09 §2.3): clk, sandbox_tree, embedded_pg, fake_backend."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest

from opagent_testing import pg as _pg


@pytest.fixture(scope="session")
def embedded_pg_cluster() -> Iterator[_pg.PgCluster]:
    base = tempfile.mkdtemp(prefix="opagent-pg-")
    init = None
    try:
        from opagent_db.schema import create_all  # optional: only when packages/db is installed

        init = create_all
    except ImportError:  # pragma: no cover - db package always present in the workspace
        pass
    cluster = _pg.start_cluster(base, init)
    yield cluster
    _pg.stop_cluster(cluster)


@pytest.fixture
def embedded_pg(embedded_pg_cluster: _pg.PgCluster) -> Iterator[_pg.ClonedDb]:
    db = _pg.clone(embedded_pg_cluster)
    yield db
    _pg.drop(embedded_pg_cluster, db)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("OPAGENT_HOME", str(home))
    monkeypatch.setenv("OPAGENT_TEST_MODE", "1")
    os.environ.setdefault("OPAGENT_TC_RESULTS", ".tc/py.json")
