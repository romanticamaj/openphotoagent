import os
import unicodedata

import pytest
from opagent_fs.executor import Action, effective_op
from opagent_fs.safepath import PathViolation, RootConfig, RootNotAllowed, RootUnavailable, Sandbox


@pytest.fixture
def sb(sandbox_tree):
    return Sandbox([RootConfig(**r) for r in sandbox_tree.roots])


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.os_windows
@pytest.mark.tc("FS-013")
def test_fs_013_windows_junction_rejected(sandbox_tree, sb):
    import subprocess

    subprocess.run(["cmd", "/c", "mklink", "/J", str(sandbox_tree.photos / "j"), "C:\\Windows"], check=True)
    with pytest.raises(PathViolation) as e:
        sb.resolve("photos", "j/win.ini")
    assert e.value.reason == "symlink"


@pytest.mark.integration
@pytest.mark.os_macos
@pytest.mark.os_windows
@pytest.mark.tc("FS-015")
def test_fs_015_case_insensitive_fs_returns_disk_case(sandbox_tree, sb):
    (sandbox_tree.photos / "2025").mkdir()
    (sandbox_tree.photos / "2025" / "a.jpg").write_bytes(b"x")
    p = sb.resolve("photos", "2025/A.JPG")
    assert p.rel == "2025/a.jpg"


@pytest.mark.integration
@pytest.mark.tc("FS-016")
def test_fs_016_nfd_on_disk_nfc_rel(sandbox_tree, sb):
    nfd = unicodedata.normalize("NFD", "Café.jpg")
    (sandbox_tree.photos / nfd).write_bytes(b"cafe")
    p = sb.resolve("photos", unicodedata.normalize("NFC", "Café.jpg"))
    assert p.rel == unicodedata.normalize("NFC", "Café.jpg")
    with sb.open_read(p) as fh:
        assert fh.read() == b"cafe"


@pytest.mark.unit
@pytest.mark.tc("FS-018")
def test_fs_018_move_from_ro_root_downgrades_to_copy(sb):
    a = Action("a", "move", "photos", "x.jpg", "sorted", "y.jpg", "h")
    op, downgraded = effective_op(sb, a, allow_move_from_ro_roots=False)
    assert (op, downgraded) == ("copy", True)
    b = Action("b", "move", "sorted", "x.jpg", "sorted", "y/x.jpg", "h")
    assert effective_op(sb, b, allow_move_from_ro_roots=False) == ("move", False)


@pytest.mark.unit
@pytest.mark.tc("FS-019")
def test_fs_019_unknown_alias(sb):
    with pytest.raises(RootNotAllowed) as e:
        sb.resolve("nope", "a.jpg")
    assert e.value.code == "FS_ROOT_NOT_ALLOWED"


@pytest.mark.unit
@pytest.mark.tc("FS-020")
def test_fs_020_violation_audited_without_absolute_path_and_permanent(sandbox_tree):
    log = []
    sb = Sandbox([RootConfig(**r) for r in sandbox_tree.roots], audit=log.append)
    with pytest.raises(PathViolation) as e:
        sb.resolve("photos", "../etc/passwd")
    assert e.value.retryable is False
    assert log == [{"event": "PATH_VIOLATION", "alias": "photos", "rel": "../etc/passwd", "reason": "dotdot"}]
    assert str(sandbox_tree.base) not in repr(log)


@pytest.mark.integration
@pytest.mark.tc("FS-021")
def test_fs_021_root_itself_is_symlink(tmp_path):
    real = tmp_path / "private_tmp"
    real.mkdir()
    (real / "a.jpg").write_bytes(b"x")
    os.symlink(real, tmp_path / "tmp_link")
    sb = Sandbox([RootConfig("photos", str(tmp_path / "tmp_link"), "ro")])
    p = sb.resolve("photos", "a.jpg")
    assert p.real == real.resolve() / "a.jpg"
    with sb.open_read(p) as fh:
        assert fh.read() == b"x"


@pytest.mark.integration
@pytest.mark.tc("FS-022")
def test_fs_022_missing_root_is_unavailable_not_violation(sandbox_tree, sb):
    os.rename(sandbox_tree.photos, sandbox_tree.base / "unplugged")
    with pytest.raises(RootUnavailable) as e:
        sb.resolve("photos", "a.jpg")
    assert e.value.code == "FS_ROOT_UNAVAILABLE" and e.value.retryable is True
    assert not isinstance(e.value, PathViolation)


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("FS-023")
def test_fs_023_refuse_overwriting_hardlinked_target(sandbox_tree, sb):
    victim = sandbox_tree.outside / "victim.txt"
    victim.write_text("keep me")
    os.link(victim, sandbox_tree.sorted / "dst.jpg")
    with pytest.raises(PathViolation) as e:
        sb.check_overwrite(sb.open_write("sorted", "dst.jpg"))
    assert e.value.reason == "hardlink"
    assert victim.read_text() == "keep me"
