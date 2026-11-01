import json
import os
from datetime import timedelta
from pathlib import Path

import blake3
import pytest
from opagent_fs.executor import Action, Executor
from opagent_fs.fsops import RealFs
from opagent_fs.journal import Journal
from opagent_fs.safepath import RootConfig, Sandbox
from opagent_testing.clock import FakeClock


def b3(p: Path) -> str:
    return blake3.blake3(p.read_bytes()).hexdigest()


@pytest.fixture
def lab(sandbox_tree, tmp_path):
    class L:
        clk = FakeClock()
        roots = [
            {"alias": "photos", "path": str(sandbox_tree.photos), "mode": "rw"},
            {"alias": "sorted", "path": str(sandbox_tree.sorted), "mode": "rw"},
        ]
        sb = Sandbox([RootConfig(**r) for r in roots])
        ex = Executor(sb, RealFs(), Journal(tmp_path / "j.db"), clock=clk)
        t = sandbox_tree

        def acts(self, n, op="copy"):
            out = []
            for i in range(n):
                p = self.t.photos / f"src/{i}.jpg"
                p.parent.mkdir(exist_ok=True)
                p.write_bytes(os.urandom(40) + bytes([i]))
                out.append(Action(f"a{i}", op, "photos", f"src/{i}.jpg", "sorted", f"o/{i}.jpg", b3(p)))
            return out

    return L()


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-002")
def test_out_undo_002_modified_targets_are_conflicts(lab):
    acts = lab.acts(4)
    lab.ex.apply("p", acts)
    for i in (1, 3):
        (lab.t.sorted / f"o/{i}.jpg").write_bytes(b"user edited")
    res = {r.action_id: r for r in lab.ex.undo("p")}
    assert [res[f"a{i}"].status for i in range(4)] == ["undone", "conflict", "undone", "conflict"]
    assert res["a1"].error == "UNDO_TARGET_MODIFIED"
    assert sorted(os.listdir(lab.t.sorted / "o")) == ["1.jpg", "3.jpg"]


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-003")
def test_out_undo_003_undo_move_restores_source(lab):
    acts = lab.acts(2, op="move")
    hashes = [a.src_hash for a in acts]
    lab.ex.apply("p", acts, move_confirmed=True)
    assert not (lab.t.photos / "src/0.jpg").exists()
    assert [r.status for r in lab.ex.undo("p")] == ["undone", "undone"]
    assert [b3(lab.t.photos / f"src/{i}.jpg") for i in range(2)] == hashes
    assert not (lab.t.sorted / "o").exists()


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-004")
def test_out_undo_004_original_location_occupied(lab):
    acts = lab.acts(1, op="move")
    lab.ex.apply("p", acts, move_confirmed=True)
    (lab.t.photos / "src/0.jpg").write_bytes(b"new file at the old place")
    [r] = lab.ex.undo("p")
    assert r.status == "conflict"
    assert (lab.t.photos / "src/0.jpg").read_bytes() == b"new file at the old place"


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-005")
def test_out_undo_005_partially_applied(lab):
    acts = lab.acts(10)
    rep = lab.ex.apply("p", acts, control=iter(["run"] * 3 + ["cancel"]).__next__)
    assert rep.status == "partially_applied"
    assert [r.action_id for r in lab.ex.undo("p")] == ["a0", "a1", "a2"]


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-006")
def test_out_undo_006_idempotent(lab):
    lab.ex.apply("p", lab.acts(3))
    assert len(lab.ex.undo("p")) == 3
    assert lab.ex.undo("p") == []


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-007")
def test_out_undo_007_trash_expired(lab):
    moved = lab.acts(1, op="move")
    lab.ex.apply("p", moved, move_confirmed=True)
    copy = Action("c0", "copy", "photos", "src/extra.jpg", "sorted", "o/extra.jpg", "")
    (lab.t.photos / "src/extra.jpg").write_bytes(b"extra")
    copy = Action("c0", "copy", "photos", "src/extra.jpg", "sorted", "o/extra.jpg", b3(lab.t.photos / "src/extra.jpg"))
    lab.ex.apply("p", [copy])
    lab.clk.advance(timedelta(days=31).total_seconds())
    purged = lab.ex.purge_trash(retention_days=30)
    assert purged == ["p"] and not (lab.t.photos / ".opagent-trash/p").exists()
    res = {r.action_id: r for r in lab.ex.undo("p")}
    assert (res["a0"].status, res["a0"].error) == ("conflict", "UNDO_TRASH_EXPIRED")
    assert res["c0"].status == "undone"


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-008")
def test_out_undo_008_undo_is_a_phased_pausable_operation(lab):
    lab.ex.apply("p", lab.acts(5))
    events: list[tuple[str, object]] = []

    class Rep:
        def start(self, op_type, total=None, unit="items"):
            events.append(("start", op_type))

        def phase(self, name):
            events.append(("phase", name))

        def progress(self, done, total=None):
            events.append(("progress", done))

        def finish(self, status):
            events.append(("finish", status))

    res = lab.ex.undo("p", reporter=Rep(), control=iter(["run", "run", "pause"]).__next__)
    assert len(res) == 2
    assert events[0] == ("start", "output.undo")
    assert [e for e in events if e[0] == "phase"] == [("phase", "verify"), ("phase", "revert")]
    assert events[-1] == ("finish", "paused")
    assert lab.ex.UNDO_PHASE_WEIGHTS == {"verify": 20, "revert": 80}
    assert len(lab.ex.undo("p")) == 3  # resume finishes the rest


@pytest.mark.integration
@pytest.mark.tc("OUT-UNDO-009")
def test_out_undo_009_sidecar_delete_or_restore_backup(lab):
    acts = lab.acts(2)
    lab.ex.apply("p0", acts)
    (lab.t.sorted / "o/1.opagent.json").write_text('{"user": "kept"}')
    side = [
        Action(
            f"s{i}",
            "sidecar_json",
            "photos",
            f"src/{i}.jpg",
            "sorted",
            f"o/{i}.opagent.json",
            "",
            payload={"schema_version": 1, "i": i},
        )
        for i in range(2)
    ]
    assert [r.status for r in lab.ex.apply("p1", side).results] == ["done", "done"]
    assert json.loads((lab.t.sorted / "o/1.opagent.json").read_text())["i"] == 1
    lab.ex.undo("p1")
    assert not (lab.t.sorted / "o/0.opagent.json").exists()
    assert json.loads((lab.t.sorted / "o/1.opagent.json").read_text()) == {"user": "kept"}
