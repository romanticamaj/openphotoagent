"""Exact journal records, result fields and recovery / purge details of the executor (OUT-JRN-003, OUT-CRASH-*,
OUT-UNDO-*, OUT-EXE-*). These pin the journal contract that recovery and undo read back (docs/02 §6.2)."""

import errno
import json
import os
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import blake3
import pytest
from opagent_fs.executor import Action, Executor, _is_lock_error, _state, effective_op
from opagent_fs.fsops import TMP_PREFIX, RealFs
from opagent_fs.journal import Journal
from opagent_fs.safepath import RootConfig, Sandbox
from opagent_testing.clock import FakeClock
from opagent_testing.fakefs import Fault, FaultFs

DAY = timedelta(days=1).total_seconds()
ABSENT = {"exists": False, "hash": None, "mtime": None}


def b3(p: Path) -> str:
    return blake3.blake3(p.read_bytes()).hexdigest()


class Lab:
    def __init__(self, tree: Any, tmp_path: Path, faults: list[Fault] | None = None) -> None:
        self.t = tree
        self.clk = FakeClock()
        roots = [
            {"alias": "photos", "path": str(tree.photos), "mode": "rw"},
            {"alias": "sorted", "path": str(tree.sorted), "mode": "rw"},
        ]
        self.sb = Sandbox([RootConfig(**r) for r in roots])
        self.j = Journal(tmp_path / "j.db")
        fs = FaultFs(RealFs(), faults or []) if faults is not None else RealFs()
        self.ex = Executor(self.sb, fs, self.j, clock=self.clk)

    def put(self, rel: str, data: bytes) -> str:
        p = self.t.photos / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return b3(p)

    def copy(self, aid: str, rel: str, dst_rel: str, data: bytes, **kw: Any) -> Action:
        return Action(aid, kw.pop("op", "copy"), "photos", rel, "sorted", dst_rel, self.put(rel, data), **kw)

    def phases(self, plan: str) -> list[tuple[str, str]]:
        return [(e["action_id"], e["phase"]) for e in self.j.entries(plan)]


@pytest.fixture
def lab(sandbox_tree, tmp_path):
    return Lab(sandbox_tree, tmp_path)


def _untmp(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace the random temp file name by a marker after checking its prefix."""
    out = []
    for s in steps:
        post = dict(s["post"])
        if "tmp" in post:
            assert post["tmp"].startswith(TMP_PREFIX)
            post["tmp"] = "<tmp>"
        out.append({**s, "post": post})
    return out


@pytest.mark.unit
@pytest.mark.tc("OUT-JRN-003")
def test_out_jrn_003_copy_intent_and_done_records_are_exact(lab):
    (lab.t.sorted / "2025").mkdir()
    a = lab.copy("c", "a.jpg", "2025/07/a.jpg", b"copy-me")
    mtime = (lab.t.photos / "a.jpg").stat().st_mtime_ns
    [r] = lab.ex.apply("p", [a]).results
    assert (r.status, r.dst_rel, r.error, r.conflict, r.downgraded) == ("done", "2025/07/a.jpg", None, "none", False)
    intent, done = lab.j.entries("p")
    assert (intent["op"], intent["phase"], done["phase"], done["ref_seq"]) == ("copy", "intent", "done", intent["seq"])
    # only the missing directory is planned; the existing "2025" is never scheduled for removal
    assert intent["args"] == {
        "src": ["photos", "a.jpg"],
        "dst": ["sorted", "2025/07/a.jpg"],
        "src_hash": a.src_hash,
        "planned_dirs": ["2025/07"],
    }
    src_state = {"exists": True, "hash": a.src_hash, "mtime": mtime}
    assert intent["pre_state"] == {"src": src_state, "dst": ABSENT}
    post = done["post_state"]
    assert _untmp(post.pop("steps")) == [
        {"op": "mkdir", "pre": {"exists": False}, "post": {"dirs": ["2025/07"]}},
        {"op": "copy", "pre": src_state, "post": {"tmp": "<tmp>"}},
        {"op": "verify", "pre": {"expected": a.src_hash}, "post": {"hash": a.src_hash}},
        {"op": "fsync", "pre": {}, "post": {"file": True, "dir": True}},
        {"op": "rename", "pre": {"exists": False}, "post": {"dst_rel": "2025/07/a.jpg", "exists": True}},
    ]
    assert post == {"dst_rel": "2025/07/a.jpg", "dst_hash": a.src_hash, "created_dirs": ["2025/07"]}


@pytest.mark.unit
@pytest.mark.tc("OUT-JRN-003")
def test_out_jrn_003_move_done_records_trash_step(lab):
    a = lab.copy("m", "m.jpg", "m.jpg", b"move-me", op="move")
    [r] = lab.ex.apply("p", [a], move_confirmed=True).results
    assert (r.status, r.conflict, r.downgraded) == ("done", "none", False)
    post = lab.j.entries("p")[-1]["post_state"]
    assert post["steps"][-1] == {
        "op": "trash_move",
        "pre": {"src_rel": "m.jpg"},
        "post": {"trash_rel": ".opagent-trash/p/m.jpg"},
    }
    assert post["trash_rel"] == ".opagent-trash/p/m.jpg"
    assert post["trashed_at"] == lab.clk.now().isoformat()
    assert (lab.t.photos / ".opagent-trash/p/m.jpg").read_bytes() == b"move-me"


@pytest.mark.unit
@pytest.mark.tc("OUT-JRN-003")
def test_out_jrn_003_state_of_a_path(tmp_path):
    p = tmp_path / "f.jpg"
    assert _state(RealFs(), p) == ABSENT
    p.write_bytes(b"data")
    assert _state(RealFs(), p) == {"exists": True, "hash": b3(p), "mtime": p.stat().st_mtime_ns}


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-020")
def test_out_exe_020_sidecar_bytes_and_journal_records(lab):
    payload = {"b": "café", "a": 1}
    s = Action("s", "sidecar_json", "photos", "a.jpg", "sorted", "side/a.opagent.json", "", payload=payload)
    [r] = lab.ex.apply("p", [s]).results
    assert (r.status, r.dst_rel, r.error, r.conflict, r.downgraded) == (
        "done",
        "side/a.opagent.json",
        None,
        "none",
        False,
    )
    data = (lab.t.sorted / "side/a.opagent.json").read_bytes()
    assert data == '{\n  "a": 1,\n  "b": "café"\n}\n'.encode()  # sorted keys, indent 2, UTF-8 not \u-escaped
    digest = blake3.blake3(data).hexdigest()
    intent, done = lab.j.entries("p")
    assert (intent["op"], intent["phase"]) == ("write_sidecar", "intent")
    assert intent["args"] == {"dst": ["sorted", "side/a.opagent.json"], "digest": digest, "planned_dirs": ["side"]}
    assert intent["pre_state"] == {"dst": ABSENT}
    post = done["post_state"]
    assert _untmp(post.pop("steps")) == [
        {"op": "mkdir", "pre": {"exists": False}, "post": {"dirs": ["side"]}},
        {"op": "write_sidecar", "pre": {"exists": False}, "post": {"tmp": "<tmp>", "hash": digest}},
        {"op": "fsync", "pre": {}, "post": {"file": True, "dir": True}},
        {"op": "rename", "pre": {"exists": False}, "post": {"dst_rel": "side/a.opagent.json", "exists": True}},
    ]
    assert post == {"dst_rel": "side/a.opagent.json", "dst_hash": digest, "backup_rel": None, "created_dirs": ["side"]}
    [again] = lab.ex.apply("p2", [s]).results
    assert (again.status, again.dst_rel, again.conflict) == ("skipped", "side/a.opagent.json", "exists_same")
    assert lab.ex.undo("p")[0].status == "undone"
    assert not (lab.t.sorted / "side").exists()  # created folder removed on undo


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-009")
def test_out_undo_009_user_sidecar_backed_up_under_trash(lab):
    (lab.t.sorted / "o").mkdir()
    (lab.t.sorted / "o/a.opagent.json").write_text('{"user": "kept"}')
    s = Action("s", "sidecar_json", "photos", "a.jpg", "sorted", "o/a.opagent.json", "", payload={"x": 1})
    assert lab.ex.apply("p", [s]).results[0].status == "done"
    backup = ".opagent-trash/p/sidecar-backup/o/a.opagent.json"
    assert (lab.t.sorted / backup).read_text() == '{"user": "kept"}'
    assert lab.j.entries("p")[-1]["post_state"]["backup_rel"] == backup


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-009")
def test_out_undo_009_missing_backup_does_not_break_undo(lab):
    (lab.t.sorted / "o").mkdir()
    (lab.t.sorted / "o/a.opagent.json").write_text('{"user": "kept"}')
    s = Action("s", "sidecar_json", "photos", "a.jpg", "sorted", "o/a.opagent.json", "", payload={"x": 1})
    lab.ex.apply("p", [s])
    os.unlink(lab.t.sorted / ".opagent-trash/p/sidecar-backup/o/a.opagent.json")
    [r] = lab.ex.undo("p")
    assert r.status == "undone"
    assert not (lab.t.sorted / "o/a.opagent.json").exists()


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-017")
def test_out_exe_017_report_status_for_pause_and_cancel(lab):
    def acts(plan):
        return [lab.copy(f"a{i}", f"{plan}/{i}.jpg", f"{plan}/{i}.jpg", plan.encode() + bytes([i])) for i in range(3)]

    assert lab.ex.apply("p0", acts("p0"), control=lambda: "cancel").status == "cancelled"
    rep = lab.ex.apply("p1", acts("p1"), control=lambda: "pause")
    assert (rep.status, rep.results) == ("paused", [])
    rep = lab.ex.apply("p2", acts("p2"), control=iter(["run", "pause"]).__next__)
    assert (rep.status, [r.status for r in rep.results]) == ("paused", ["done"])
    rep = lab.ex.apply("p3", acts("p3"), control=iter(["run", "cancel"]).__next__)
    assert (rep.status, [r.status for r in rep.results]) == ("partially_applied", ["done"])
    assert lab.ex.apply("p4", acts("p4")).status == "applied"


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-017")
def test_out_exe_017_unconfirmed_moves_fail_the_plan(lab):
    moves = [lab.copy(f"m{i}", f"m{i}.jpg", f"o/m{i}.jpg", bytes([i]) * 4, op="move") for i in range(2)]
    rep = lab.ex.apply("p", moves)
    assert rep.status == "failed"
    assert [(r.status, r.error) for r in rep.results] == [("failed", "MOVE_NOT_CONFIRMED")] * 2
    ok = lab.copy("c", "c.jpg", "o/c.jpg", b"c")
    rep = lab.ex.apply("q", [ok, *moves])
    assert (rep.status, [r.status for r in rep.results]) == ("partially_applied", ["done", "failed", "failed"])


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-007")
def test_out_exe_007_default_policy_is_rename_and_fail_reports_dst(lab):
    (lab.t.sorted / "o").mkdir()
    (lab.t.sorted / "o/a.jpg").write_bytes(b"different")
    a = lab.copy("a", "a.jpg", "o/a.jpg", b"mine")  # no conflict_policy given
    [r] = lab.ex.apply("p", [a]).results
    assert (r.status, r.dst_rel, r.conflict) == ("done", "o/a (1).jpg", "exists_diff")
    f = Action("f", "copy", "photos", "a.jpg", "sorted", "o/a.jpg", a.src_hash, conflict_policy="fail")
    [r] = lab.ex.apply("q", [f]).results
    assert (r.status, r.dst_rel, r.error, r.conflict) == ("failed", "o/a.jpg", "DST_EXISTS", "exists_diff")


@pytest.mark.chaos
@pytest.mark.tc("OUT-EXE-008")
def test_out_exe_008_redirect_is_journaled(sandbox_tree, tmp_path):
    def racer(src, dst):
        Path(dst).write_bytes(b"racer")

    lab = Lab(sandbox_tree, tmp_path, faults=[Fault("rename_noreplace", nth=1, before=racer)])
    a = lab.copy("a", "a.jpg", "a.jpg", b"mine")
    assert lab.ex.apply("p", [a]).results[0].dst_rel == "a (1).jpg"
    redirect = [e for e in lab.j.entries("p") if e["phase"] == "redirect"]
    assert [(e["action_id"], e["op"], e["post_state"]) for e in redirect] == [("a", "copy", {"dst_rel": "a (1).jpg"})]


@pytest.mark.chaos
@pytest.mark.tc("OUT-CRASH-004")
def test_out_crash_004_failed_verify_is_journaled_as_rolled_back(sandbox_tree, tmp_path):
    def corrupt(p):
        if Path(p).name.startswith(TMP_PREFIX):
            Path(p).write_bytes(b"corrupted")

    lab = Lab(sandbox_tree, tmp_path, faults=[Fault("read_hash", nth=2, before=corrupt)])
    a = lab.copy("a", "a.jpg", "new/a.jpg", b"aaa")
    [r] = lab.ex.apply("p", [a]).results
    assert (r.status, r.error) == ("failed", "COPY_FAILED")
    entries = lab.j.entries("p")
    assert [(e["phase"], e["post_state"]) for e in entries[1:]] == [("rolled_back", {"error": "COPY_FAILED"})]
    assert lab.j.open_intents() == []
    assert not (lab.t.sorted / "new").exists()


def _open_intent(lab: Lab, aid: str, op: str, dst_rel: str, key: str, value: str, planned: list[str]) -> None:
    lab.j.intent(
        "p",
        aid,
        op,
        {"src": ["photos", f"{aid}.jpg"], "dst": ["sorted", dst_rel], key: value, "planned_dirs": planned},
        {},
    )


@pytest.mark.chaos
@pytest.mark.tc("OUT-CRASH-006")
def test_out_crash_006_recovery_rolls_forward_committed_copy(lab):
    h = lab.put("c.jpg", b"committed")
    _open_intent(lab, "c", "copy", "r/c.jpg", "src_hash", h, ["r"])
    (lab.t.sorted / "r").mkdir()
    (lab.t.sorted / "r/c.jpg").write_bytes(b"committed")
    (lab.t.sorted / "r" / f"{TMP_PREFIX}stray").write_bytes(b"half")
    assert lab.ex.recover() == {"completed": ["c"], "rolled_back": [], "tmp_removed": 1}
    done = lab.j.entries("p")[-1]
    assert done["phase"] == "done"
    assert done["post_state"] == {
        "dst_rel": "r/c.jpg",
        "dst_hash": h,
        "created_dirs": ["r"],
        "recovered": True,
        "steps": [],
    }
    assert lab.ex.recover() == {"completed": [], "rolled_back": [], "tmp_removed": 0}


@pytest.mark.chaos
@pytest.mark.tc("OUT-CRASH-009")
def test_out_crash_009_recovery_rolls_forward_committed_sidecar(lab):
    data = b'{"x": 1}\n'
    _open_intent(lab, "s", "write_sidecar", "s.opagent.json", "digest", blake3.blake3(data).hexdigest(), [])
    (lab.t.sorted / "s.opagent.json").write_bytes(data)
    assert lab.ex.recover()["completed"] == ["s"]
    assert (lab.t.sorted / "s.opagent.json").read_bytes() == data


@pytest.mark.chaos
@pytest.mark.tc("OUT-CRASH-002")
def test_out_crash_002_recovery_rollback_record(lab):
    _open_intent(lab, "c", "copy", "gone/c.jpg", "src_hash", "0" * 64, ["gone"])
    (lab.t.sorted / "gone").mkdir()
    assert lab.ex.recover() == {"completed": [], "rolled_back": ["c"], "tmp_removed": 0}
    last = lab.j.entries("p")[-1]
    assert (last["action_id"], last["op"], last["phase"], last["post_state"]) == (
        "c",
        "copy",
        "rolled_back",
        {"error": "RECOVERED_ROLLBACK"},
    )
    assert not (lab.t.sorted / "gone").exists()
    assert lab.j.open_intents() == []


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-007")
def test_out_undo_007_purge_uses_default_retention_and_strict_cutoff(lab):
    lab.ex.apply("old", [lab.copy("o", "o.jpg", "x/o.jpg", b"old", op="move")], move_confirmed=True)
    lab.clk.advance(10 * DAY)
    lab.ex.apply("new", [lab.copy("n", "n.jpg", "x/n.jpg", b"new", op="move")], move_confirmed=True)
    lab.clk.advance(20 * DAY)
    assert lab.ex.purge_trash() == []  # exactly 30 days old: not yet expired
    lab.clk.advance(1)
    assert lab.ex.purge_trash() == ["old"]  # default retention is 30 days; the 20-day-old plan is kept
    assert not (lab.t.photos / ".opagent-trash/old").exists()
    assert (lab.t.photos / ".opagent-trash/new/n.jpg").exists()
    last = lab.j.entries("old")[-1]
    assert (last["action_id"], last["op"], last["phase"], last["post_state"]) == (
        "*",
        "purge_trash",
        "purged",
        {"aliases": ["photos"]},
    )
    assert lab.ex.purge_trash() == []  # a purged plan is never purged twice
    lab.clk.advance(10 * DAY)
    assert lab.ex.purge_trash() == ["new"]


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-004")
def test_out_undo_004_occupied_source_error_code(lab):
    lab.ex.apply("p", [lab.copy("m", "m.jpg", "o/m.jpg", b"moved", op="move")], move_confirmed=True)
    (lab.t.photos / "m.jpg").write_bytes(b"new file at the old place")
    [r] = lab.ex.undo("p")
    assert (r.status, r.dst_rel, r.error) == ("conflict", "o/m.jpg", "UNDO_SOURCE_OCCUPIED")


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-008")
def test_out_undo_008_completed_undo_finishes_succeeded(lab):
    lab.ex.apply("p", [lab.copy("a", "a.jpg", "o/a.jpg", b"a")])
    events: list[tuple[str, object]] = []

    class Rep:
        def start(self, op_type, total=None, unit="items"):
            events.append(("start", total))

        def phase(self, name):
            events.append(("phase", name))

        def progress(self, done, total=None):
            events.append(("progress", done))

        def finish(self, status):
            events.append(("finish", status))

    assert [r.status for r in lab.ex.undo("p", reporter=Rep())] == ["undone"]
    assert events == [("start", 1), ("phase", "verify"), ("phase", "revert"), ("progress", 0), ("finish", "succeeded")]


@pytest.mark.unit
@pytest.mark.tc("OUT-EXE-016")
def test_out_exe_016_lock_errors_are_classified(tmp_path):
    assert _is_lock_error(OSError(errno.EBUSY, "busy"))
    assert _is_lock_error(OSError(errno.ETXTBSY, "busy"))
    for winerror in (32, 33):  # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
        e = OSError(errno.EACCES, "denied")
        e.winerror = winerror  # type: ignore[attr-defined]
        assert _is_lock_error(e)
    assert not _is_lock_error(OSError(errno.EACCES, "denied"))
    e = OSError(errno.EACCES, "denied")
    e.winerror = 5  # type: ignore[attr-defined]  # ERROR_ACCESS_DENIED is permanent
    assert not _is_lock_error(e)


@pytest.mark.unit
@pytest.mark.tc("FS-018")
def test_fs_018_effective_op_downgrades_by_default(sandbox_tree):
    sb = Sandbox([RootConfig(**r) for r in sandbox_tree.roots])
    assert sb.roots["photos"].mode == "ro"
    a = Action("m", "move", "photos", "a.jpg", "sorted", "a.jpg", "h")
    assert effective_op(sb, a) == ("copy", True)
    assert effective_op(sb, a, allow_move_from_ro_roots=True) == ("move", False)


@pytest.mark.unit
@pytest.mark.tc("OUT-JRN-001")
def test_out_jrn_001_journal_opens_in_new_folder_and_across_threads(tmp_path):
    path = tmp_path / "state" / "nested" / "j.db"
    j = Journal(path)
    seqs: list[int] = []
    t = threading.Thread(target=lambda: seqs.append(j.intent("p", "a", "copy", {}, {})))
    t.start()
    t.join()
    assert seqs == [1]
    assert [e["seq"] for e in Journal(path).entries("p")] == [1]  # reopening an existing journal works


@pytest.mark.unit
@pytest.mark.tc("OUT-CRASH-011")
def test_out_crash_011_open_intents_follow_phases(tmp_path):
    j = Journal(tmp_path / "j.db")
    s1 = j.intent("p", "done", "copy", {"k": 1}, {"pre": 1})
    j.done(s1, {})
    j.record("p", "rb", "copy", "rolled_back", {"error": "X"}, ref=j.intent("p", "rb", "copy", {}, {}))
    s3 = j.intent("p", "open", "move", {"k": 3}, {"pre": 3})
    j.record("p", "open", "move", "redirect", {"dst_rel": "a (1).jpg"})
    assert j.open_intents() == [
        ("p", "open", {"seq": s3, "op": "move", "args": {"k": 3}, "pre_state": {"pre": 3}}, "a (1).jpg")
    ]
    assert json.loads(json.dumps(j.entries("p")[-1]["post_state"])) == {"dst_rel": "a (1).jpg"}
