import errno
import os
import stat
import tempfile
from pathlib import Path

import blake3
import pytest

from opagent_core.planner import PlannedAction, assign_collisions
from opagent_fs.executor import Action, Executor
from opagent_fs.fsops import RealFs
from opagent_fs.journal import Journal
from opagent_fs.safepath import RootConfig, Sandbox
from opagent_testing.fakefs import Fault, FaultFs


def b3(p: Path) -> str:
    return blake3.blake3(p.read_bytes()).hexdigest()


class Env:
    def __init__(self, photos: Path, sorted_: Path, tmp: Path, faults=(), photos_mode="ro"):
        self.photos, self.sorted = photos, sorted_
        self.sb = Sandbox([RootConfig("photos", str(photos), photos_mode), RootConfig("sorted", str(sorted_), "rw")])
        self.fs = FaultFs(RealFs(), list(faults))
        self.ex = Executor(self.sb, self.fs, Journal(tmp / "j.db"))

    def put(self, rel: str, data: bytes, root: Path | None = None) -> Path:
        p = (root or self.photos) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def act(self, rel: str, dst: str, op="copy", policy="rename", aid=None) -> Action:
        return Action(aid or f"a:{rel}", op, "photos", rel, "sorted", dst, b3(self.photos / rel), conflict_policy=policy)


@pytest.fixture
def env(sandbox_tree, tmp_path):
    return Env(sandbox_tree.photos, sandbox_tree.sorted, tmp_path)


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-003")
def test_out_exe_003_preserves_mtime_and_mode(env):
    src = env.put("a.jpg", os.urandom(100))
    os.chmod(src, 0o640)
    os.utime(src, ns=(1_500_000_000_000_000_000, 1_500_000_000_123_456_789))
    env.ex.apply("p", [env.act("a.jpg", "x/a.jpg")])
    dst = env.sorted / "x/a.jpg"
    assert dst.stat().st_mtime_ns == src.stat().st_mtime_ns
    assert stat.S_IMODE(dst.stat().st_mode) == stat.S_IMODE(src.stat().st_mode)


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-004")
def test_out_exe_004_source_changed_after_plan(env):
    env.put("a.jpg", b"v1")
    a = env.act("a.jpg", "a.jpg")
    env.put("a.jpg", b"v2-modified")
    [r] = env.ex.apply("p", [a]).results
    assert (r.status, r.error) == ("skipped", "PLAN_STALE")
    assert not (env.sorted / "a.jpg").exists()


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-005")
def test_out_exe_005_existing_same_hash_skipped(env):
    env.put("a.jpg", b"same")
    env.put("a.jpg", b"same", env.sorted)
    [r] = env.ex.apply("p", [env.act("a.jpg", "a.jpg", policy="rename")]).results
    assert (r.status, r.conflict) == ("skipped", "exists_same")
    assert sorted(p.name for p in env.sorted.iterdir()) == ["a.jpg"]


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-006")
def test_out_exe_006_rename_suffix_deterministic(env):
    env.put("IMG_0001.jpg", b"new")
    env.put("IMG_0001.jpg", b"existing-1", env.sorted)
    env.put("IMG_0001 (1).jpg", b"existing-2", env.sorted)
    [r] = env.ex.apply("p", [env.act("IMG_0001.jpg", "IMG_0001.jpg")]).results
    assert (r.status, r.dst_rel, r.conflict) == ("done", "IMG_0001 (2).jpg", "exists_diff")
    assert (env.sorted / "IMG_0001 (2).jpg").read_bytes() == b"new"
    assert (env.sorted / "IMG_0001.jpg").read_bytes() == b"existing-1"


@pytest.mark.unit
@pytest.mark.tc("OUT-EXE-007")
@pytest.mark.parametrize(("policy", "status"), [("skip", "skipped"), ("fail", "failed"),
                                                ("overwrite_if_same_hash", "skipped")])
def test_out_exe_007_policies_on_exists_diff(env, policy, status):
    env.put("a.jpg", b"new")
    env.put("b.jpg", b"other")
    env.put("a.jpg", b"different", env.sorted)
    res = env.ex.apply("p", [env.act("a.jpg", "a.jpg", policy=policy), env.act("b.jpg", "b.jpg")]).results
    assert res[0].status == status and res[0].conflict == "exists_diff"
    assert res[1].status == "done"  # other actions continue
    assert (env.sorted / "a.jpg").read_bytes() == b"different"  # never overwritten


@pytest.mark.integration
@pytest.mark.chaos
@pytest.mark.tc("OUT-EXE-008")
def test_out_exe_008_dst_appears_at_commit(sandbox_tree, tmp_path):
    def racer(src, dst):
        Path(dst).write_bytes(b"racer")

    env = Env(sandbox_tree.photos, sandbox_tree.sorted, tmp_path,
              faults=[Fault("rename_noreplace", nth=1, before=racer)])
    env.put("a.jpg", b"mine")
    [r] = env.ex.apply("p", [env.act("a.jpg", "a.jpg")]).results
    assert (env.sorted / "a.jpg").read_bytes() == b"racer"  # never overwritten
    assert r.status == "done" and r.dst_rel == "a (1).jpg"
    assert (env.sorted / "a (1).jpg").read_bytes() == b"mine"
    assert not list(env.sorted.glob(".opagent-tmp-*"))


@pytest.mark.unit
@pytest.mark.tc("OUT-EXE-009")
def test_out_exe_009_in_plan_collisions_get_suffix_at_plan_time():
    acts = [PlannedAction("a1", "x/IMG.jpg", "2025/IMG.jpg", "h1", None),
            PlannedAction("a2", "y/IMG.jpg", "2025/IMG.jpg", "h2", None),
            PlannedAction("a3", "z/IMG.jpg", "2025/IMG.jpg", "h1", None),  # same content as a1 -> same file
            PlannedAction("a4", "w/img.JPG", "2025/img.JPG", "h4", None)]
    out = assign_collisions(acts, case_insensitive=True)
    assert [(a.dst_rel, a.conflict) for a in out] == [
        ("2025/IMG.jpg", "none"), ("2025/IMG (1).jpg", "name_collision"), ("2025/IMG.jpg", "none"),
        ("2025/img (2).JPG", "name_collision")]
    assert assign_collisions(acts, case_insensitive=True) == out  # deterministic


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-011")
def test_out_exe_011_move_goes_to_trash(sandbox_tree, tmp_path):
    env = Env(sandbox_tree.photos, sandbox_tree.sorted, tmp_path, photos_mode="rw")
    env.put("d/a.jpg", b"moving")
    [r] = env.ex.apply("plan9", [env.act("d/a.jpg", "a.jpg", op="move")], move_confirmed=True).results
    assert r.status == "done"
    assert not (env.photos / "d/a.jpg").exists()
    assert (env.photos / ".opagent-trash/plan9/d/a.jpg").read_bytes() == b"moving"
    assert (env.sorted / "a.jpg").read_bytes() == b"moving"
    assert any(e["op"] == "move" and e["phase"] == "done" and e["post_state"]["trash_rel"]
               for e in env.ex.journal.entries("plan9"))


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-013")
def test_out_exe_013_cross_device_move_never_renames_across(tmp_path):
    shm = Path(tempfile.mkdtemp(dir="/dev/shm", prefix="opagent-x-"))
    assert os.stat(shm).st_dev != os.stat(tmp_path).st_dev
    photos = tmp_path / "photos"
    photos.mkdir()
    env = Env(photos, shm, tmp_path, photos_mode="rw")
    env.put("a.jpg", b"xdev")
    [r] = env.ex.apply("p", [env.act("a.jpg", "a.jpg", op="move")], move_confirmed=True).results
    assert r.status == "done" and (shm / "a.jpg").read_bytes() == b"xdev"
    assert (photos / ".opagent-trash/p/a.jpg").exists() and not (photos / "a.jpg").exists()
    assert "write_tmp_copy" in env.fs.log  # copy + verify, then trash within the source fs


@pytest.mark.chaos
@pytest.mark.tc("OUT-EXE-014")
def test_out_exe_014_disk_full_waits_then_resumes(sandbox_tree, tmp_path):
    env = Env(sandbox_tree.photos, sandbox_tree.sorted, tmp_path, faults=[Fault("write_tmp_copy", nth=2, err=errno.ENOSPC)])
    acts = [env.act(r, r) for r in (env.put(f"{i}.jpg", os.urandom(50)).name for i in range(3))]
    rep = env.ex.apply("p", acts)
    assert rep.status == "waiting" and rep.waiting_reason == "DISK_INSUFFICIENT_OUTPUT"
    assert [r.status for r in rep.results] == ["done"]
    assert not (env.sorted / acts[1].dst_rel).exists() and not list(env.sorted.glob(".opagent-tmp-*"))
    rep2 = env.ex.apply("p", acts)  # space freed: resume from that action
    assert rep2.status == "applied"
    assert [r.status for r in rep2.results] == ["skipped", "done", "done"]
    assert rep2.results[0].conflict == "exists_same"
