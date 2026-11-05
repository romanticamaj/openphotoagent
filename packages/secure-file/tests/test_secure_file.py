"""DESK-043/044 (回歸, ADR-0024): secrets are owner-only on every OS, including a data folder on D:\\.

``os.chmod(p, 0o600)`` does nothing useful on Windows, so run/api.json, the worker key and the credstore fallback kept
whatever ACL they inherited: on a non-system drive that is readable and writable by every local user. ``parent`` below
is deliberately wide, because a pytest tmp dir is already private and would prove nothing."""

import os
import sys

import pytest
from opagent_secure import private_dir, restrict, write_private
from opagent_testing.perms import assert_owner_only, widen


@pytest.fixture
def parent(tmp_path):
    d = tmp_path / "wide"
    d.mkdir()
    widen(d, 0o777)
    return d


@pytest.mark.unit
@pytest.mark.tc("DESK-043")
def test_desk_043_the_oracle_rejects_a_plain_file(parent):
    f = parent / "plain"
    f.write_bytes(b"x")
    widen(f, 0o644)
    with pytest.raises(AssertionError):
        assert_owner_only(f, 0o600)


@pytest.mark.unit
@pytest.mark.tc("DESK-043")
def test_desk_043_write_private_is_owner_only_under_a_wide_parent(parent):
    f = parent / "api.json"
    assert write_private(f, b'{"port": 1}') is True
    assert f.read_bytes() == b'{"port": 1}'
    assert_owner_only(f, 0o600)
    assert [p.name for p in parent.iterdir()] == ["api.json"]  # no temporary file left behind


@pytest.mark.unit
@pytest.mark.tc("DESK-043")
def test_desk_043_owner_only_before_the_first_byte(parent):
    seen = []

    def check(tmp):
        assert tmp.stat().st_size == 0
        assert_owner_only(tmp, 0o600)
        seen.append(tmp.parent)

    write_private(parent / "worker.key", b"secret", on_open=check)
    assert seen == [parent]


@pytest.mark.unit
@pytest.mark.tc("DESK-043")
def test_desk_043_replacing_a_wide_file_narrows_it(parent):
    f = parent / "remote.json"
    f.write_bytes(b"old")
    widen(f, 0o644)
    write_private(f, b"new")
    assert f.read_bytes() == b"new"
    assert_owner_only(f, 0o600)


@pytest.mark.unit
@pytest.mark.tc("DESK-043")
def test_desk_043_a_failed_write_leaves_the_old_file_and_no_temporary(parent):
    f = parent / "api.json"
    write_private(f, b"old")

    def boom(_tmp):
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        write_private(f, b"new", on_open=boom)
    assert f.read_bytes() == b"old"
    assert [p.name for p in parent.iterdir()] == ["api.json"]


@pytest.mark.unit
@pytest.mark.tc("DESK-044")
def test_desk_044_restrict_tightens_an_existing_file_and_folder(parent):
    d = parent / "secrets"
    d.mkdir()
    widen(d, 0o755)
    f = d / "mcp-pat-default"
    f.write_bytes(b"token")
    widen(f, 0o644)
    assert restrict(d) is True and restrict(f) is True
    assert_owner_only(d, 0o700)
    assert_owner_only(f, 0o600)
    assert f.read_bytes() == b"token"


@pytest.mark.unit
@pytest.mark.tc("DESK-044")
def test_desk_044_private_dir_creates_parents_and_is_idempotent(parent):
    d = parent / "a" / "run"
    assert private_dir(d) is True
    assert private_dir(d) is True
    assert_owner_only(d, 0o700)
    write_private(d / "api.json", b"{}")
    (d / "api.json").unlink()  # the owner can still replace and remove what is inside


@pytest.mark.unit
@pytest.mark.os_windows
@pytest.mark.tc("DESK-044")
def test_desk_044_a_new_file_in_a_private_dir_inherits_the_protection(parent):
    d = parent / "backups"
    private_dir(d)
    (d / "copy.bin").write_bytes(b"x")  # shutil.copy2 into a backup folder: no explicit ACL of its own
    from opagent_testing.perms import sddl

    aces = sddl(d / "copy.bin")
    assert "SY" in aces and "BU" not in aces and "AU" not in aces and "WD" not in aces


@pytest.mark.unit
@pytest.mark.os_windows
@pytest.mark.tc("DESK-044")
def test_desk_044_a_volume_without_acls_degrades_with_a_warning(parent, monkeypatch, caplog):
    assert sys.platform == "win32"
    from opagent_secure import _win32

    monkeypatch.setattr(_win32, "has_acls", lambda _p: False)
    f = parent / "api.json"
    assert write_private(f, b"{}") is False  # FAT, exFAT: written anyway, and the caller knows
    assert f.read_bytes() == b"{}"
    assert restrict(f) is False
    assert "SECRET_UNPROTECTED_VOLUME" in caplog.text


@pytest.mark.unit
@pytest.mark.os_windows
@pytest.mark.tc("DESK-044")
def test_desk_044_an_acl_that_does_not_stick_fails_closed(parent, monkeypatch):
    assert sys.platform == "win32"
    from opagent_secure import _win32

    monkeypatch.setattr(_win32, "_dacl_of", lambda _h: "D:(A;;FA;;;WD)")
    f = parent / "worker.key"
    with pytest.raises(PermissionError):
        write_private(f, b"secret")
    assert os.listdir(parent) == []  # neither the secret nor a temporary file
