"""`trash` output action (09b TRASH-*; 02 output_rule.action, 11 D-2): the file moves to the same root's
`.opagent-trash/<plan_id>/<rel>` by an atomic rename, journaled, and undo puts it back."""

from datetime import timedelta

import pytest
from opagent_fs.executor import Action, Executor
from opagent_testing.clock import FakeClock


def trash_action(tree, rel, data, helpers, alias="sorted"):
    root = tree.sorted if alias == "sorted" else tree.photos
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return Action(f"t-{rel}", "trash", alias, rel, alias, rel, helpers.b3(p)), p


@pytest.mark.integration
@pytest.mark.tc("TRASH-001", "TRASH-002")
def test_trash_001_002_moves_into_the_roots_trash_by_rename(env, helpers):
    a, p = trash_action(env.tree, "dup/IMG_1.jpg", b"one", helpers)
    h = helpers.b3(p)
    rep = env.ex.apply("plan1", [a])
    assert [r.status for r in rep.results] == ["done"]
    t = env.tree.sorted / ".opagent-trash" / "plan1" / "dup" / "IMG_1.jpg"
    assert not p.exists() and t.exists() and helpers.b3(t) == h
    ops = [e for e in env.log if e.startswith(("rename", "copy", "write_tmp"))]
    assert ops and all(e.startswith("rename") for e in ops)  # same root: a rename, never a copy
    phases = [(e["op"], e["phase"]) for e in env.journal.entries("plan1")]
    assert ("trash_move", "intent") in phases and ("trash_move", "done") in phases


@pytest.mark.integration
@pytest.mark.tc("TRASH-003")
def test_trash_003_undo_restores_in_place(env, helpers):
    a, p = trash_action(env.tree, "x/IMG_2.jpg", b"two", helpers)
    before = helpers.b3(p)
    env.ex.apply("plan2", [a])
    res = env.ex.undo("plan2")
    assert [r.status for r in res] == ["undone"]
    assert p.exists() and helpers.b3(p) == before
    assert not list((env.tree.sorted / ".opagent-trash").rglob("*.jpg"))


@pytest.mark.integration
@pytest.mark.tc("TRASH-004")
def test_trash_004_read_only_root_is_refused_not_downgraded(env, helpers):
    a, p = trash_action(env.tree, "IMG_3.jpg", b"three", helpers, alias="photos")
    rep = env.ex.apply("plan3", [a])
    assert [(r.status, r.error) for r in rep.results] == [("failed", "FS_NOT_WRITABLE")]
    assert p.exists() and not (env.tree.photos / ".opagent-trash").exists()
    assert not list(env.tree.sorted.rglob("IMG_3.jpg"))  # not turned into a copy either


@pytest.mark.integration
@pytest.mark.tc("TRASH-006")
def test_trash_006_retention_purge_then_undo_reports_expired(env, helpers):
    clk = FakeClock()
    ex = Executor(env.sb, env.fs, env.journal, clock=clk)
    a, p = trash_action(env.tree, "y/IMG_4.jpg", b"four", helpers)
    b, q = trash_action(env.tree, "y/IMG_5.jpg", b"five", helpers)
    ex.apply("old", [a])
    clk.advance(timedelta(days=29).total_seconds())
    ex.apply("new", [b])
    assert ex.purge_trash(retention_days=30) == []  # nothing old enough yet: undo still possible
    clk.advance(timedelta(days=2).total_seconds())
    assert ex.purge_trash(retention_days=30) == ["old"]
    assert not (env.tree.sorted / ".opagent-trash" / "old").exists()
    assert [(r.status, r.error) for r in ex.undo("old")] == [("conflict", "UNDO_TRASH_EXPIRED")]
    assert [r.status for r in ex.undo("new")] == ["undone"] and q.exists()


@pytest.mark.integration
@pytest.mark.tc("TRASH-003")
def test_trash_003_undo_never_overwrites_a_new_file_in_the_old_place(env, helpers):
    a, p = trash_action(env.tree, "z/IMG_6.jpg", b"six", helpers)
    env.ex.apply("plan6", [a])
    p.write_bytes(b"someone put a new file here")
    res = env.ex.undo("plan6")
    assert [(r.status, r.error, r.dst_rel) for r in res] == [("conflict", "UNDO_SOURCE_OCCUPIED", "z/IMG_6.jpg")]
    assert p.read_bytes() == b"someone put a new file here"
    assert (env.tree.sorted / ".opagent-trash" / "plan6" / "z" / "IMG_6.jpg").read_bytes() == b"six"


@pytest.mark.integration
@pytest.mark.tc("TRASH-006")
def test_trash_006_undo_after_purge_names_the_photo(env, helpers):
    clk = FakeClock()
    ex = Executor(env.sb, env.fs, env.journal, clock=clk)
    a, _ = trash_action(env.tree, "w/IMG_7.jpg", b"seven", helpers)
    ex.apply("p7", [a])
    clk.advance(timedelta(days=31).total_seconds())
    ex.purge_trash(retention_days=30)
    assert [(r.status, r.error, r.dst_rel) for r in ex.undo("p7")] == [
        ("conflict", "UNDO_TRASH_EXPIRED", "w/IMG_7.jpg")
    ]
