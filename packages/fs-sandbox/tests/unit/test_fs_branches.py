"""Supporting unit tests for fs-sandbox error paths and platform branches (coverage gate, 09 §5.6)."""

import errno
import os
import sys
from pathlib import Path

import blake3
import opagent_fs.fsops as fsops
import opagent_fs.safepath as sp
import pytest
from opagent_fs import crash
from opagent_fs.executor import Action, Executor
from opagent_fs.fsops import RealFs
from opagent_fs.journal import Journal
from opagent_fs.safepath import PathViolation, RootConfig, Sandbox
from opagent_testing.fakefs import Fault, FaultFs


def b3(p: Path) -> str:
    return blake3.blake3(p.read_bytes()).hexdigest()


@pytest.fixture
def sb(sandbox_tree):
    return Sandbox([RootConfig(**r) for r in sandbox_tree.roots])


def test_large_file_copied_in_chunks(tmp_path):
    src = tmp_path / "big.bin"
    src.write_bytes(os.urandom(fsops.CHUNK * 2 + 17))
    tmp = RealFs().write_tmp_copy(src, tmp_path)
    assert tmp.read_bytes() == src.read_bytes()


def test_tmp_removed_when_copy_fails(tmp_path):
    with pytest.raises(OSError):
        RealFs().write_tmp_copy(tmp_path / "missing.jpg", tmp_path)
    assert not list(tmp_path.glob(".opagent-tmp-*"))
    (tmp_path / "ro").mkdir()
    os.chmod(tmp_path / "ro", 0o500)
    try:
        if os.geteuid() != 0:
            with pytest.raises(OSError):
                RealFs().write_tmp_bytes(b"x", tmp_path / "ro")
    finally:
        os.chmod(tmp_path / "ro", 0o700)
    with pytest.raises(OSError):
        RealFs().write_tmp_bytes(b"x", tmp_path / "nope")


class FakeLibc:
    def __init__(self, ret: int, err: int) -> None:
        self.ret, self.err = ret, err

    def renamex_np(self, *a):
        return self.ret

    def renameat2(self, *a):
        return self.ret


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_rename_noreplace_libc_paths(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("a")
    monkeypatch.setattr(fsops, "_LIBC", FakeLibc(-1, 0))
    for err, exc in ((errno.EEXIST, FileExistsError), (errno.EACCES, PermissionError)):
        monkeypatch.setattr(fsops.ctypes, "get_errno", lambda e=err: e)
        with pytest.raises(exc):
            RealFs().rename_noreplace(a, b)
    monkeypatch.setattr(fsops.ctypes, "get_errno", lambda: errno.EINVAL)
    RealFs().rename_noreplace(a, b)  # falls back to link + unlink
    assert b.read_text() == "a" and not a.exists()
    monkeypatch.setattr(fsops, "_LIBC", FakeLibc(0, 0))
    RealFs().rename_noreplace(b, a)  # libc call "succeeds" (fake) -> nothing else happens
    assert b.exists()


def test_rename_fallback_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(fsops, "_LIBC", None)
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("a")
    b.write_text("b")
    with pytest.raises(FileExistsError):
        RealFs().rename_noreplace(a, b)
    assert b.read_text() == "b"


def test_windows_branches(tmp_path, monkeypatch, sandbox_tree, sb):
    monkeypatch.setattr(sys, "platform", "win32")
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("a")
    RealFs().rename_noreplace(a, b)
    RealFs().fsync_dir(tmp_path)
    (sandbox_tree.photos / "w.jpg").write_bytes(b"w")
    with sb.open_read(sb.resolve("photos", "w.jpg")) as fh:
        assert fh.read() == b"w"


def test_case_insensitive_lookup(monkeypatch, sandbox_tree, sb):
    monkeypatch.setattr(sp, "CASE_INSENSITIVE_FS", True)
    (sandbox_tree.photos / "Dir").mkdir()
    (sandbox_tree.photos / "Dir" / "a.jpg").write_bytes(b"x")
    assert sb.resolve("photos", "Dir/a.jpg").rel == "Dir/a.jpg"
    monkeypatch.setattr(sp.os.path, "lexists", lambda p: True)
    assert sb.resolve("photos", "dir/x.jpg").rel in ("Dir/x.jpg", "dir/x.jpg")


def test_listdir_errors_are_tolerated(monkeypatch, sandbox_tree, sb):
    def boom(_):
        raise PermissionError

    monkeypatch.setattr(sp.os, "listdir", boom)
    assert sb.resolve("photos", "nothing/here.jpg").rel == "nothing/here.jpg"
    monkeypatch.setattr(sp, "CASE_INSENSITIVE_FS", True)
    (sandbox_tree.photos / "a.jpg").write_bytes(b"x")
    assert sb.resolve("photos", "a.jpg").rel == "a.jpg"


def test_escape_guard_and_lstat_errors(monkeypatch, sandbox_tree, sb):
    (sandbox_tree.photos / "file.jpg").write_bytes(b"x")
    with pytest.raises(PathViolation) as e:
        sb.resolve("photos", "file.jpg/child.jpg")
    assert e.value.reason == "not_a_directory"
    monkeypatch.setattr(sp, "check_rel", lambda rel, alias="": ["..", "outside"])
    with pytest.raises(PathViolation) as e:
        sb.resolve("photos", "x")
    assert e.value.reason == "escape"


def test_lstat_other_errors_propagate(monkeypatch, sb):
    def boom(_):
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(sp.os, "lstat", boom)
    with pytest.raises(OSError):
        sb.resolve("photos", "a.jpg")


def test_open_read_missing_parts_raise(sandbox_tree, sb):
    with pytest.raises(FileNotFoundError):
        sb.open_read(sb.resolve("photos", "nodir/a.jpg"))
    (sandbox_tree.photos / "d").mkdir()
    with pytest.raises(FileNotFoundError):
        sb.open_read(sb.resolve("photos", "d/a.jpg"))
    (sandbox_tree.photos / "d" / "real.jpg").write_bytes(b"r")
    p = sb.resolve("photos", "d/a.jpg")
    os.symlink(sandbox_tree.photos / "d" / "real.jpg", sandbox_tree.photos / "d" / "a.jpg")
    with pytest.raises(PathViolation):
        sb.open_read(p)


def test_check_overwrite_single_link_ok(sandbox_tree, sb):
    (sandbox_tree.sorted / "one.jpg").write_bytes(b"1")
    sb.check_overwrite(sb.open_write("sorted", "one.jpg"))
    sb.check_overwrite(sb.open_write("sorted", "absent.jpg"))


def test_crash_point_in_process(monkeypatch):
    calls = []
    monkeypatch.setenv("OPAGENT_TEST_MODE", "1")
    monkeypatch.setenv("OPAGENT_CRASH_AT", "here")
    monkeypatch.setenv("OPAGENT_CRASH_NTH", "2")
    monkeypatch.setattr(crash.os, "_exit", calls.append)
    monkeypatch.setattr(crash, "_counts", {})
    crash.crash_point("elsewhere")
    crash.crash_point("here")
    assert calls == []
    crash.crash_point("here")
    assert calls == [137]


def _ex(sandbox_tree, tmp_path, faults=(), **kw):
    sbx = Sandbox([RootConfig(**r) for r in sandbox_tree.roots])
    return Executor(sbx, FaultFs(RealFs(), list(faults)), Journal(tmp_path / "j.db"), **kw)


def test_executor_error_paths(sandbox_tree, tmp_path):
    (sandbox_tree.photos / "a.jpg").write_bytes(b"aaa")
    h = b3(sandbox_tree.photos / "a.jpg")
    roots_rw = [dict(r, mode="rw") for r in sandbox_tree.roots]
    ex = Executor(Sandbox([RootConfig(**r) for r in roots_rw]), RealFs(), Journal(tmp_path / "m.db"))
    assert ex.apply("p", [Action("m", "move", "photos", "a.jpg", "sorted", "a.jpg", h)]).results[0].error == (
        "MOVE_NOT_CONFIRMED"
    )
    ex2 = _ex(sandbox_tree, tmp_path, [Fault("write_tmp_copy", err=errno.EACCES)])
    rep = ex2.apply("p", [Action("c", "copy", "photos", "a.jpg", "sorted", "a.jpg", h)])
    assert (rep.status, rep.results[0].error) == ("failed", "COPY_FAILED")

    def corrupt(p):
        if Path(p).name.startswith(".opagent-tmp-"):
            Path(p).write_bytes(b"corrupted")

    ex3 = _ex(sandbox_tree, tmp_path, [Fault("read_hash", nth=2, before=corrupt)])
    rep3 = ex3.apply("q", [Action("c", "copy", "photos", "a.jpg", "sorted", "v.jpg", h)])
    assert rep3.results[0].error == "COPY_FAILED" and not (sandbox_tree.sorted / "v.jpg").exists()
    ex4 = _ex(sandbox_tree, tmp_path, [Fault("rename_noreplace", err=errno.EEXIST)])
    r4 = ex4.apply("r", [Action("c", "copy", "photos", "a.jpg", "sorted", "w.jpg", h, conflict_policy="skip")])
    assert r4.results[0].error == "DST_EXISTS"
    ex5 = _ex(sandbox_tree, tmp_path, [Fault("rename_noreplace", err=errno.EIO)])
    assert ex5.apply("s", [Action("c", "copy", "photos", "a.jpg", "sorted", "x.jpg", h)]).results[0].error == (
        "COPY_FAILED"
    )
    assert ex5.purge_trash() == []
    ex5._rmtree(tmp_path / "does-not-exist")
