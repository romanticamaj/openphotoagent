"""SCAN-027 (回歸): the scan cache commits in batches, and a crash costs at most one batch of re-hashing.

``HashCache.put`` committed per file, and each WAL commit is an fsync. Measured on the reference machine: about
590 ms per file on a 4 TB HDD, 150 ms with batching; about 10 ms either way on NVMe, so this matters where a worker's
data folder sits on a spinning disk. The cache is only an optimisation, so losing the tail of a batch to a crash is
safe: those files are hashed again and nothing else depends on the row."""

import sqlite3
from datetime import UTC
from pathlib import Path

import pytest
from opagent_fs.safepath import RootConfig, Sandbox
from opagent_worker import scanner
from opagent_worker.scanner import HashCache, scan

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic/IMG_0001.jpg"
BATCH = 8  # the real one is larger, which would only make these tests slow
N = BATCH * 2 + 7


@pytest.fixture(autouse=True)
def _small_batch(monkeypatch):
    monkeypatch.setattr(scanner, "CACHE_BATCH", BATCH)


def _many(root: Path, n: int) -> None:
    data = FX.read_bytes()
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (root / f"{i:05d}.jpg").write_bytes(data + i.to_bytes(4, "big"))


def _scan(sandbox_tree, cache, reporter=None):
    sb = Sandbox([RootConfig(**r) for r in sandbox_tree.roots])
    return scan(sb, "photos", "big", ["**/*.jpg"], [], UTC, cache=cache, reporter=reporter)


def _rows(path: Path) -> int:
    db = sqlite3.connect(path)  # a second connection sees only what a crash would leave behind
    try:
        return int(db.execute("SELECT count(*) FROM entry").fetchone()[0])
    finally:
        db.close()


class Reporter:
    """Records how far the durable cache lags behind the scan at every file."""

    def __init__(self, path: Path, stop_at: int | None = None) -> None:
        self.path, self.stop_at, self.lag = path, stop_at, 0

    def start(self, *_a):
        pass

    def phase(self, *_a):
        pass

    def finish(self, *_a):
        pass

    def progress(self, done, total):
        if total is None:
            return
        self.lag = max(self.lag, done - _rows(self.path))
        if done == self.stop_at:
            raise RuntimeError("scan interrupted")


@pytest.mark.unit
@pytest.mark.tc("SCAN-027")
def test_scan_027_commits_once_per_batch_not_per_file(sandbox_tree, tmp_path):
    _many(sandbox_tree.photos / "big", N)
    cache = HashCache(tmp_path / "scan-cache.db")
    stmts: list[str] = []
    cache._db.set_trace_callback(stmts.append)
    assert len(_scan(sandbox_tree, cache).entries) == N
    commits = sum(1 for s in stmts if s.strip().upper().startswith("COMMIT"))
    assert commits <= N // BATCH + 1, commits


@pytest.mark.unit
@pytest.mark.tc("SCAN-027")
def test_scan_027_a_finished_scan_is_fully_durable_so_the_rescan_reads_nothing(sandbox_tree, tmp_path):
    _many(sandbox_tree.photos / "big", N)
    path = tmp_path / "scan-cache.db"
    _scan(sandbox_tree, HashCache(path))
    assert _rows(path) == N  # without close(): the end of the scan is itself a commit point
    assert _scan(sandbox_tree, HashCache(path)).bytes_read == 0  # SCAN-004 still holds across a restart


@pytest.mark.unit
@pytest.mark.tc("SCAN-027")
def test_scan_027_a_crash_loses_at_most_one_batch(sandbox_tree, tmp_path):
    _many(sandbox_tree.photos / "big", N)
    path = tmp_path / "scan-cache.db"
    rep = Reporter(path)
    _scan(sandbox_tree, HashCache(path), reporter=rep)
    assert 0 < rep.lag <= BATCH  # kill -9 at any file: only the uncommitted tail is hashed again


@pytest.mark.unit
@pytest.mark.tc("SCAN-027")
def test_scan_027_an_interrupted_scan_keeps_what_it_hashed(sandbox_tree, tmp_path):
    _many(sandbox_tree.photos / "big", N)
    path = tmp_path / "scan-cache.db"
    stop = BATCH + 3
    with pytest.raises(RuntimeError):
        _scan(sandbox_tree, HashCache(path), reporter=Reporter(path, stop_at=stop))
    assert _rows(path) == stop  # an exception is not a crash: the partial batch is flushed
    r = _scan(sandbox_tree, HashCache(path))
    assert len(r.entries) == N and r.bytes_read == sum(e.size for e in r.entries[stop:])
