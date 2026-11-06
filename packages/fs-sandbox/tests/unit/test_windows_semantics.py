"""Windows semantics the first CI run on windows-latest exposed (回歸). Each runs on every OS by simulating the
Windows behaviour, so a Linux PR catches a regression before the Windows runner does."""

import os

import pytest
from opagent_fs import fsops
from opagent_fs import safepath as sp
from opagent_fs.safepath import PathViolation, check_rel

WINDOWS_INVALID = ["\x1f.jpg", "a\x01b.jpg", "a<b.jpg", "a>b.jpg", 'a"b.jpg', "a|b.jpg", "a?b.jpg", "a*b.jpg"]
TRAILING = ["name.", "name ", "dir./a.jpg", "dir /a.jpg"]


@pytest.mark.unit
@pytest.mark.tc("FS-028")
@pytest.mark.parametrize("rel", WINDOWS_INVALID + TRAILING)
def test_fs_028_windows_invalid_names_are_a_violation_not_an_oserror(rel):
    """09a FS-024: a path either resolves under its root or is a PathViolation. On Windows, characters 0-31 and
    <>\"|?* cannot appear in a name (lstat raised WinError 123), and a trailing dot or space is silently stripped,
    so two different names would alias one file."""
    with pytest.raises(PathViolation) as e:
        check_rel(rel, windows=True)
    assert e.value.reason == "windows_invalid_name"


@pytest.mark.unit
@pytest.mark.tc("FS-028")
def test_fs_028_other_oses_keep_names_that_are_legal_there():
    assert check_rel("what?.jpg", windows=False) == ["what?.jpg"]  # an existing Linux/macOS photo stays reachable
    assert check_rel("a.jpg", windows=True) == ["a.jpg"]


@pytest.mark.unit
@pytest.mark.tc("FS-029")
def test_fs_029_fsync_file_opens_a_writable_handle_on_windows(monkeypatch, tmp_path):
    """Windows' fsync (_commit) needs a handle with write access; a read-only one raised EBADF, failing every copy."""
    f = tmp_path / "x"
    f.write_bytes(b"x")
    seen: list[int] = []
    real_open = os.open
    monkeypatch.setattr(fsops.sys, "platform", "win32")
    monkeypatch.setattr(fsops.os, "open", lambda p, flags, *a: seen.append(flags) or real_open(p, os.O_RDONLY))
    monkeypatch.setattr(fsops.os, "fsync", lambda fd: None)
    fsops.RealFs().fsync_file(f)
    assert seen and seen[0] & os.O_RDWR, seen


@pytest.mark.unit
@pytest.mark.tc("FS-030")
def test_fs_030_child_of_a_file_is_not_a_directory_on_every_os(monkeypatch, sandbox_tree):
    """POSIX reports ENOTDIR for `file.jpg/child.jpg`; Windows reports "path not found", which SafePath took for a
    write target. The answer must not depend on the OS."""
    (sandbox_tree.photos / "file.jpg").write_bytes(b"x")
    sb = sp.Sandbox([sp.RootConfig(**r) for r in sandbox_tree.roots])
    real_lstat = os.lstat

    def windows_lstat(p, *a, **k):
        if str(p).endswith("child.jpg"):
            raise FileNotFoundError(3, "The system cannot find the path specified")
        return real_lstat(p, *a, **k)

    monkeypatch.setattr(sp.os, "lstat", windows_lstat)
    with pytest.raises(PathViolation) as e:
        sb.resolve("photos", "file.jpg/child.jpg")
    assert e.value.reason == "not_a_directory"
