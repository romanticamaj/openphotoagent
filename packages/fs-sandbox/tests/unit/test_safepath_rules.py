import os

import pytest
from opagent_fs.safepath import PathViolation, RootConfig, Sandbox


@pytest.fixture
def sb(sandbox_tree):
    (sandbox_tree.outside / "secret.txt").write_text("secret", encoding="utf-8")
    return Sandbox([RootConfig(**r) for r in sandbox_tree.roots])


def _reason(sb, rel, alias="photos"):
    with pytest.raises(PathViolation) as e:
        sb.resolve(alias, rel)
    return e.value.reason


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("FS-004")
def test_fs_004_no_url_decoding(sb, sandbox_tree):
    p = sb.resolve("photos", "%2e%2e/x")
    assert p.rel == "%2e%2e/x"
    assert str(p.real).startswith(str(sandbox_tree.photos.resolve()))


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("FS-005")
def test_fs_005_nul_rejected(sb):
    assert _reason(sb, "a\x00.jpg") == "nul"


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("FS-006")
@pytest.mark.parametrize("rel", ["C:foo", "\\\\server\\share\\x", "\\\\?\\C:\\x", "\\\\.\\PhysicalDrive0", "c:/x"])
def test_fs_006_drive_unc_device_rejected(sb, rel):
    assert _reason(sb, rel) in ("absolute", "drive", "backslash")


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("FS-007")
@pytest.mark.parametrize("rel", ["a.jpg:secret", "a.jpg::$DATA", "dir:x/a.jpg"])
def test_fs_007_ntfs_ads_colon_rejected(sb, rel):
    assert _reason(sb, rel) == "colon"


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("FS-008")
@pytest.mark.parametrize("rel", ["a\\..\\b", "a\\b.jpg"])
def test_fs_008_backslash_rejected(sb, rel):
    assert _reason(sb, rel) == "backslash"


@pytest.mark.unit
@pytest.mark.tc("FS-009")
@pytest.mark.parametrize("rel", ["CON/a.jpg", "x/nul.txt", "COM1", "lpt9.jpg/x", "Aux"])
def test_fs_009_reserved_names_rejected(sb, rel):
    assert _reason(sb, rel) == "reserved_name"


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("FS-010")
def test_fs_010_symlink_middle_segment(sb, sandbox_tree):
    os.symlink(sandbox_tree.outside, sandbox_tree.photos / "link")
    assert _reason(sb, "link/secret.txt") == "symlink"


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("FS-011")
def test_fs_011_final_symlink_inside_root_still_rejected(sb, sandbox_tree):
    (sandbox_tree.photos / "b.jpg").write_bytes(b"x")
    os.symlink(sandbox_tree.photos / "b.jpg", sandbox_tree.photos / "a.jpg")
    assert _reason(sb, "a.jpg") == "symlink"


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.chaos
@pytest.mark.tc("FS-012")
def test_fs_012_toctou_swap_dir_for_symlink(sb, sandbox_tree):
    d = sandbox_tree.photos / "dir"
    d.mkdir()
    (d / "a.jpg").write_bytes(b"photo")
    (sandbox_tree.outside / "a.jpg").write_bytes(b"outside!")
    p = sb.resolve("photos", "dir/a.jpg")
    # attacker swaps the directory for a symlink after resolve, before open
    os.rename(d, sandbox_tree.photos / "dir.old")
    os.symlink(sandbox_tree.outside, d)
    with pytest.raises(PathViolation) as e, sb.open_read(p):
        pass
    assert e.value.reason in ("symlink", "escape")


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("FS-014")
def test_fs_014_prefix_false_positive(tmp_path):
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos2").mkdir()
    sb = Sandbox([RootConfig("photos", str(tmp_path / "photos"), "ro")])
    from opagent_fs.safepath import is_within

    assert not is_within(tmp_path / "photos", tmp_path / "photos2" / "x")
    assert is_within(tmp_path / "photos", tmp_path / "photos" / "x")
    assert sb.resolve("photos", "x").rel == "x"
