"""rename(noreplace) on Linux and the side-effect classification of RecordingFs (OUT-EXE-008, OUT-JRN-001)."""

import errno
import sys

import opagent_fs.fsops as fsops
import pytest
from opagent_fs.fsops import RealFs, RecordingFs


@pytest.mark.unit
@pytest.mark.tc("OUT-EXE-008")
def test_out_exe_008_linux_uses_atomic_renameat2_with_relative_paths(tmp_path, monkeypatch):
    if sys.platform.startswith("linux"):
        assert fsops._LIBC is not None and hasattr(fsops._LIBC, "renameat2")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a").write_text("a")
    (tmp_path / "taken").write_text("keep")
    RealFs().rename_noreplace(fsops.Path("a"), fsops.Path("b"))  # relative to the cwd (AT_FDCWD)
    assert (tmp_path / "b").read_text() == "a" and not (tmp_path / "a").exists()
    with pytest.raises(FileExistsError):
        RealFs().rename_noreplace(fsops.Path("b"), fsops.Path("taken"))
    assert (tmp_path / "taken").read_text() == "keep" and (tmp_path / "b").exists()


class Renameat2Only:
    """A libc whose renameat2 is unsupported by the filesystem (EINVAL) and that has no renamex_np."""

    def renameat2(self, *a):
        return -1


@pytest.mark.unit
@pytest.mark.tc("OUT-EXE-008")
def test_out_exe_008_linux_fallback_when_renameat2_unsupported(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(fsops, "_LIBC", Renameat2Only())
    monkeypatch.setattr(fsops.ctypes, "get_errno", lambda: errno.EINVAL)
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("a")
    RealFs().rename_noreplace(a, b)
    assert b.read_text() == "a" and not a.exists()
    a.write_text("new")
    with pytest.raises(FileExistsError):
        RealFs().rename_noreplace(a, b)
    assert b.read_text() == "a"


@pytest.mark.unit
@pytest.mark.tc("OUT-JRN-001")
def test_out_jrn_001_side_effect_methods():
    assert {
        "makedirs",
        "write_tmp_copy",
        "fsync_file",
        "fsync_dir",
        "rename_noreplace",
        "unlink",
        "rmdir",
    } == RecordingFs.SIDE_EFFECTS
    read_only = {"read_hash", "exists", "is_empty_dir"}
    assert not (read_only & RecordingFs.SIDE_EFFECTS)
