"""Q-029 (回歸): the worker's result outbox never leaves a rollback journal next to it.

The outbox was the only worker SQLite file in rollback-journal mode: every write created ``outbox.db-journal`` for
a moment, and IMG-002 (nothing but the worker's WAL-mode bookkeeping may appear on disk) caught one mid-write.
WAL also lets the worker loop append while a flush is reading."""

import sqlite3
from pathlib import Path

import pytest


@pytest.mark.unit
@pytest.mark.tc("Q-029")
def test_q_029_outbox_is_wal_and_writes_leave_no_rollback_journal(tmp_path: Path) -> None:
    from opagent_worker.agent import WorkerAgent

    w = WorkerAgent(None, [], tmp_path / "w")
    # created in WAL at construction, while single-threaded: two threads racing to create it could leave one
    # connection in rollback mode (PRAGMA journal_mode=WAL fails silently under a lock), as seen again on CI
    probe = sqlite3.connect(tmp_path / "w" / "outbox.db")
    assert probe.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    probe.close()
    db = w._outbox()
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    held = sqlite3.connect(tmp_path / "w" / "outbox.db")  # a writer mid-transaction, as the loop would be
    held.execute("BEGIN IMMEDIATE")
    held.execute("INSERT INTO outbox(k, body) VALUES ('a', '{}')")
    names = {p.name for p in (tmp_path / "w").iterdir()}
    held.rollback()
    held.close()
    db.close()
    assert "outbox.db-journal" not in names, names
