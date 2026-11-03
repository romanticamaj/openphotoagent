"""Release channels in the updater manifests (09b §9.5 UPD-010; 08 §5.3)."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("gum", ROOT / "tools/gen_updater_manifest.py")
gum = importlib.util.module_from_spec(_s)
_s.loader.exec_module(gum)


def artefacts(d):
    for n in (
        "OpenPhotoAgent_1.1.0_x64-setup.nsis.zip",
        "OpenPhotoAgent.app.tar.gz",
        "OpenPhotoAgent_1.1.0.AppImage.tar.gz",
    ):
        (d / n).write_bytes(b"x")
        (d / (n + ".sig")).write_text("sig")


@pytest.mark.tc("UPD-010")
def test_upd_010_stable_manifest_refuses_prereleases(tmp_path):
    artefacts(tmp_path)
    with pytest.raises(SystemExit, match="pre-release"):
        gum.build("1.1.0-beta.1", tmp_path, "https://x", "", "2026-11-03T00:00:00Z", channel="stable")
    beta = gum.build("1.1.0-beta.1", tmp_path, "https://x", "", "2026-11-03T00:00:00Z", channel="beta")
    assert beta["version"] == "1.1.0-beta.1"
    stable = gum.build("1.1.0", tmp_path, "https://x", "", "2026-11-03T00:00:00Z", channel="stable")
    assert stable["version"] == "1.1.0" and len(stable["platforms"]) == 3
