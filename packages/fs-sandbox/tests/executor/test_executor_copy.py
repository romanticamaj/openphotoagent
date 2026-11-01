import os
import stat

import pytest

EXPECTED = [
    "journal.intent",
    "makedirs",
    "write_tmp_copy",
    "read_hash",
    "fsync_file",
    "fsync_dir",
    "rename_noreplace",
    "journal.done",
]


@pytest.mark.unit
@pytest.mark.tc("OUT-JRN-001")
def test_out_jrn_001_intent_committed_before_first_side_effect(env):
    a = env.photo("a.jpg", b"hello")
    env.ex.apply("plan1", [a])
    side = [i for i, c in enumerate(env.log) if c in env.fs.SIDE_EFFECTS]
    assert side, env.log
    assert "journal.intent" in env.log
    assert env.log.index("journal.intent") < side[0]
    # intent is durable: a fresh connection sees it
    from opagent_fs.journal import Journal

    rows = Journal(env.journal.path).entries("plan1")
    assert rows and rows[0]["op"] == "copy"


@pytest.mark.unit
@pytest.mark.tc("OUT-EXE-001")
def test_out_exe_001_copy_happy_path_call_sequence(env):
    a = env.photo("a.jpg", b"hello world")
    res = env.ex.apply("plan1", [a])
    assert [r.status for r in res] == ["done"]
    seq = [c for c in env.log if c != "exists"]
    # read_hash of the src before the intent is the precheck (no side effect)
    assert seq[seq.index("journal.intent") :] == EXPECTED
    dst = env.tree.sorted / a.dst_rel
    assert dst.read_bytes() == b"hello world"
    assert not list(env.tree.sorted.rglob(".opagent-tmp-*"))


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-002")
def test_out_exe_002_source_unchanged(env, helpers):
    b3 = helpers.b3
    a = env.photo("2025/a.jpg", os.urandom(4096))
    src = env.tree.photos / "2025/a.jpg"
    os.chmod(src, 0o644)
    os.utime(src, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    before = (b3(src), src.stat().st_mtime_ns, src.stat().st_size, stat.S_IMODE(src.stat().st_mode))
    assert [r.status for r in env.ex.apply("p", [a])] == ["done"]
    after = (b3(src), src.stat().st_mtime_ns, src.stat().st_size, stat.S_IMODE(src.stat().st_mode))
    assert after == before


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-001")
def test_out_undo_001_undo_copy_restores_tree(env, helpers):
    snapshot = helpers.snapshot
    actions = [env.photo(f"d{i}/IMG_{i}.jpg", os.urandom(1000 + i)) for i in range(3)]
    before = snapshot(env.tree.sorted)
    photos_before = snapshot(env.tree.photos)
    assert all(r.status == "done" for r in env.ex.apply("p1", actions))
    assert snapshot(env.tree.sorted) != before
    res = env.ex.undo("p1")
    assert [r.status for r in res] == ["undone"] * 3
    assert snapshot(env.tree.sorted) == before  # created folders removed too
    assert snapshot(env.tree.photos) == photos_before
