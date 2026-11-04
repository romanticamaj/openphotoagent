"""Desktop data-folder layout version (VERSIONING §4 `layout_version`; 09b §9.4 MIG-002)."""

import json
import os
import stat

import pytest
from opagent_cli.layout import LAYOUT_VERSION, LayoutTooNew, ensure_layout


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def v0_folder(home):
    """A data folder as v0.x left it: no layout.json, permissive modes from an older umask."""
    (home / "run").mkdir(parents=True)
    (home / "run" / "api.json").write_text(json.dumps({"port": 47800}), encoding="utf-8")
    os.chmod(home / "run", 0o755)
    os.chmod(home / "run" / "api.json", 0o644)
    (home / "secrets").mkdir()
    (home / "secrets" / "mcp-pat-default").write_text("opagent_pat_x", encoding="utf-8")
    os.chmod(home / "secrets", 0o755)
    os.chmod(home / "secrets" / "mcp-pat-default", 0o644)
    (home / "worker").mkdir()
    (home / "worker" / "journal.db").write_bytes(b"keep me")


@pytest.mark.tc("MIG-002")
def test_mig_002_layout_migration(tmp_path):
    home = tmp_path / "data"
    v0_folder(home)
    done = ensure_layout(home)
    assert done == {"from": 0, "to": LAYOUT_VERSION, "backup": done["backup"]}
    assert json.loads((home / "layout.json").read_text(encoding="utf-8"))["layout_version"] == LAYOUT_VERSION == 1
    assert mode(home / "run") == 0o700 and mode(home / "run" / "api.json") == 0o600
    assert mode(home / "secrets") == 0o700 and mode(home / "secrets" / "mcp-pat-default") == 0o600
    assert (home / "worker" / "journal.db").read_bytes() == b"keep me"  # nothing moved or lost
    backup = home / done["backup"]
    assert (
        backup.exists()
        and "run/api.json" in json.loads((backup / "manifest.json").read_text(encoding="utf-8"))["files"]
    )
    assert ensure_layout(home) is None  # idempotent: already current
    (home / "layout.json").write_text(json.dumps({"layout_version": LAYOUT_VERSION + 1}), encoding="utf-8")
    with pytest.raises(LayoutTooNew) as e:
        ensure_layout(home)
    assert e.value.code == "DATA_LAYOUT_TOO_NEW" and e.value.found == LAYOUT_VERSION + 1
    assert {f["type"] for f in e.value.fixes} == {"open_settings", "open_docs"}  # a way out, not a bare refusal


@pytest.mark.tc("MIG-002")
def test_mig_002_new_folder(tmp_path):
    home = tmp_path / "fresh"
    assert ensure_layout(home) == {"from": None, "to": LAYOUT_VERSION, "backup": None}
    assert json.loads((home / "layout.json").read_text(encoding="utf-8"))["layout_version"] == LAYOUT_VERSION
