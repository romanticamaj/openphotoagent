"""Global pytest fixtures (docs/09 §2.3): clk, sandbox_tree, embedded_pg, fake_backend."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest

from opagent_testing import pg as _pg

try:  # Hypothesis profiles (docs/09 §2.1): ci=200, nightly=10k, fuzz=1M examples
    from hypothesis import HealthCheck, settings

    _hc = [HealthCheck.too_slow, HealthCheck.function_scoped_fixture]
    settings.register_profile("ci", max_examples=200, deadline=None, suppress_health_check=_hc)
    settings.register_profile("nightly", max_examples=10_000, deadline=None, suppress_health_check=_hc)
    settings.register_profile("fuzz", max_examples=1_000_000, deadline=None, suppress_health_check=_hc)
    settings.load_profile(os.environ.get("OPAGENT_HYPOTHESIS_PROFILE", "ci"))
except ImportError:  # pragma: no cover
    pass


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


@pytest.fixture
def clk():  # type: ignore[no-untyped-def]
    from opagent_testing.clock import FakeClock

    return FakeClock()


class SandboxTree:
    """tmp tree with roots/photos (ro), roots/sorted (rw) and outside/ (docs/09 §3.3)."""

    def __init__(self, base: os.PathLike[str]) -> None:
        from pathlib import Path

        self.base = Path(base)
        self.photos = self.base / "roots" / "photos"
        self.sorted = self.base / "roots" / "sorted"
        self.outside = self.base / "outside"
        for p in (self.photos, self.sorted, self.outside):
            p.mkdir(parents=True, exist_ok=True)
        self.roots = [
            {"alias": "photos", "path": str(self.photos), "mode": "ro"},
            {"alias": "sorted", "path": str(self.sorted), "mode": "rw"},
        ]


@pytest.fixture
def sandbox_tree(tmp_path):  # type: ignore[no-untyped-def]
    return SandboxTree(tmp_path)
