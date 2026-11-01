"""Boundary cases of SafePath lexical rules, error classification and platform lookups (FS-009, FS-012, FS-013,
FS-015, FS-017, FS-019, FS-022, FS-024)."""

import errno
import os
from types import SimpleNamespace

import opagent_fs.safepath as sp
import pytest
from opagent_fs.safepath import (
    MAX_NAME_BYTES,
    PathViolation,
    RootConfig,
    RootNotAllowed,
    RootUnavailable,
    Sandbox,
    _is_reparse_point,
    check_rel,
)


def reason(rel: str) -> str:
    with pytest.raises(PathViolation) as e:
        check_rel(rel)
    return e.value.reason


@pytest.mark.unit
@pytest.mark.tc("FS-024")
def test_fs_024_name_length_boundary_is_255_bytes():
    assert MAX_NAME_BYTES == 255
    assert check_rel("d/" + "a" * 255) == ["d", "a" * 255]
    assert reason("d/" + "a" * 256) == "name_too_long"
    assert check_rel("é" * 127 + "a") == ["é" * 127 + "a"]  # 255 UTF-8 bytes
    assert reason("é" * 128) == "name_too_long"  # 256 UTF-8 bytes


@pytest.mark.unit
@pytest.mark.tc("FS-024")
def test_fs_024_dot_and_empty_segments_are_dropped():
    assert check_rel("a/./b//c/.") == ["a", "b", "c"]
    assert check_rel("") == []


@pytest.mark.unit
@pytest.mark.tc("FS-009")
@pytest.mark.parametrize(
    "name",
    ["CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))],
)
def test_fs_009_every_reserved_device_name(name):
    for seg in (name, name.lower(), f"{name}.txt", f"{name} .jpg", f"{name}.tar.gz"):
        assert reason(f"x/{seg}") == "reserved_name", seg


@pytest.mark.unit
@pytest.mark.tc("FS-009")
@pytest.mark.parametrize("seg", ["COM0", "COM10", "LPT0", "LPT10", "CONX", "XCON", "console.jpg", "nul1.txt"])
def test_fs_009_near_reserved_names_allowed(seg):
    assert check_rel(f"x/{seg}") == ["x", seg]


@pytest.mark.unit
@pytest.mark.tc("FS-019")
def test_fs_019_error_classes_are_permanent_or_transient():
    assert (RootNotAllowed.code, RootNotAllowed.retryable) == ("FS_ROOT_NOT_ALLOWED", False)
    assert (RootUnavailable.code, RootUnavailable.retryable) == ("FS_ROOT_UNAVAILABLE", True)
    assert (PathViolation.code, PathViolation.retryable) == ("PATH_VIOLATION", False)


@pytest.mark.unit
@pytest.mark.tc("FS-017")
def test_fs_017_roots_are_read_only_by_default(tmp_path):
    cfg = RootConfig("photos", str(tmp_path))
    assert cfg.mode == "ro"
    with pytest.raises(PathViolation) as e:
        Sandbox([cfg]).open_write("photos", "a.jpg")
    assert e.value.reason == "read_only_root"


@pytest.mark.unit
@pytest.mark.tc("FS-013")
def test_fs_013_reparse_point_attribute():
    assert _is_reparse_point(SimpleNamespace(st_file_attributes=0x400))  # type: ignore[arg-type]
    assert _is_reparse_point(SimpleNamespace(st_file_attributes=0x410))  # type: ignore[arg-type]
    assert not _is_reparse_point(SimpleNamespace(st_file_attributes=0x10))  # type: ignore[arg-type]
    assert not _is_reparse_point(SimpleNamespace(st_file_attributes=0x1))  # type: ignore[arg-type]  # READONLY
    assert not _is_reparse_point(SimpleNamespace())  # type: ignore[arg-type]  # POSIX stat has no attributes


@pytest.mark.unit
@pytest.mark.tc("FS-024")
def test_fs_024_too_long_full_path_from_lstat(monkeypatch, sandbox_tree):
    sb = Sandbox([RootConfig(**r) for r in sandbox_tree.roots])

    def too_long(_):
        raise OSError(errno.ENAMETOOLONG, "File name too long")

    monkeypatch.setattr(sp.os, "lstat", too_long)
    with pytest.raises(PathViolation) as e:
        sb.resolve("photos", "a.jpg")
    assert e.value.reason == "name_too_long"


@pytest.mark.unit
@pytest.mark.tc("FS-012")
def test_fs_012_final_component_swapped_for_symlink_reason(sandbox_tree):
    sb = Sandbox([RootConfig(**r) for r in sandbox_tree.roots])
    (sandbox_tree.photos / "d").mkdir()
    (sandbox_tree.photos / "d" / "real.jpg").write_bytes(b"r")
    p = sb.resolve("photos", "d/a.jpg")
    os.symlink(sandbox_tree.photos / "d" / "real.jpg", sandbox_tree.photos / "d" / "a.jpg")
    with pytest.raises(PathViolation) as e:
        sb.open_read(p)
    assert (e.value.reason, e.value.alias, e.value.rel) == ("symlink", "photos", "d/a.jpg")


@pytest.mark.unit
@pytest.mark.tc("FS-015")
@pytest.mark.parametrize(
    ("names", "part", "expected"),
    [
        (["food"], "Food", "food"),  # single case-insensitive match: disk spelling
        (["Food", "food"], "Food", "Food"),  # exact spelling wins
        (["food", "FOOD"], "Food", "Food"),  # ambiguous: keep the requested spelling
        (["other"], "Food", "Food"),
    ],
)
def test_fs_015_case_insensitive_disk_name(monkeypatch, tmp_path, names, part, expected):
    monkeypatch.setattr(sp, "CASE_INSENSITIVE_FS", True)
    with monkeypatch.context() as m:
        m.setattr(sp.os.path, "lexists", lambda p: True)
        m.setattr(sp.os, "listdir", lambda p: list(names))
        got = Sandbox._disk_name(tmp_path, part)
    assert got == expected


@pytest.mark.unit
@pytest.mark.tc("FS-015")
def test_fs_015_case_sensitive_fs_keeps_requested_spelling(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "CASE_INSENSITIVE_FS", False)
    with monkeypatch.context() as m:
        m.setattr(sp.os.path, "lexists", lambda p: True)
        m.setattr(sp.os, "listdir", lambda p: ["food"])
        got = Sandbox._disk_name(tmp_path, "Food")
    assert got == "Food"
