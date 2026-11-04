"""Global pytest fixtures (docs/09 §2.3): clk, sandbox_tree, embedded_pg, fake_backend."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

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
    if sys.platform == "win32":
        # mkdtemp's owner-only ACL locks out initdb, which drops to a restricted token on Windows; a plain mkdir
        # under %TEMP% inherits the usual ACL (first windows-latest run: "could not create directory")
        base = str(Path(tempfile.gettempdir()) / f"opagent-pg-{uuid.uuid4().hex[:8]}")
        os.mkdir(base)
    else:
        base = tempfile.mkdtemp(prefix="opagent-pg-")
    init = None
    try:
        from opagent_db.schema import create_all  # optional: only when packages/db is installed

        init = create_all
    except ImportError:  # pragma: no cover - db package always present in the workspace
        pass
    cluster = _pg.start_cluster(base, init)
    try:
        yield cluster
    finally:
        _pg.stop_cluster(cluster)
        shutil.rmtree(base, ignore_errors=True)  # a cluster per session would otherwise pile up in the temp dir


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


PG_FIXTURES = frozenset({"embedded_pg", "embedded_pg_cluster"})


@pytest.hookimpl(tryfirst=True)  # before `-m` deselection, so the added marker is what selection sees
def pytest_itemcollected(item: pytest.Item) -> None:
    """PG-015: anything that needs the real PostgreSQL is an integration test (09 §2.1), whatever else it is."""
    names = set(getattr(item, "fixturenames", ()))
    if names & PG_FIXTURES and not item.get_closest_marker("integration"):
        item.add_marker(pytest.mark.integration)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """OS-specific cases (09a §2 `os` column) only run on their OS; the OS matrix in CI covers the rest."""
    import sys

    here = {"win32": "os_windows", "darwin": "os_macos"}.get(sys.platform, "os_linux")
    keep: list[pytest.Item] = []
    drop: list[pytest.Item] = []
    for it in items:
        marks = {m.name for m in it.iter_markers() if m.name.startswith("os_")}
        (drop if marks and here not in marks else keep).append(it)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


class ApiServer:
    """A real uvicorn server in a background thread (port 0) so concurrent clients hit the real stack."""

    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        import threading

        import uvicorn

        self.config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> ApiServer:
        self.thread.start()
        import time

        for _ in range(500):
            if self.server.started:
                break
            time.sleep(0.01)  # noqa: TID251 - test harness waiting for a real server thread
        return self

    @property
    def url(self) -> str:
        sock = self.server.servers[0].sockets[0]
        return f"http://127.0.0.1:{sock.getsockname()[1]}"

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(10)


@pytest.fixture
def api_server():  # type: ignore[no-untyped-def]
    return ApiServer
