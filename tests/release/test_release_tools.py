"""Release tooling that runs in CI (08 §4.1 D6, §5.3): installer size gate and the Tauri updater manifest."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=ROOT)


@pytest.mark.unit
@pytest.mark.tc("DESK-030")
def test_desk_030_installer_over_200_mb_fails_the_build(tmp_path):
    ok = tmp_path / "OpenPhotoAgent_0.1.0_x64-setup.exe"
    big = tmp_path / "OpenPhotoAgent_0.1.0_amd64.AppImage"
    with ok.open("wb") as f:
        f.truncate(150 * 1024 * 1024)
    r = _run("tools/check_bundle_size.py", str(ok))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "150.0 MB" in r.stdout and "target 150" in r.stdout
    with big.open("wb") as f:
        f.truncate(200 * 1024 * 1024 + 1)
    r = _run("tools/check_bundle_size.py", str(ok), str(big))
    assert r.returncode == 1
    assert "AppImage" in r.stdout and "> 200 MB" in r.stdout
    r = _run("tools/check_bundle_size.py", str(tmp_path / "missing.dmg"))
    assert r.returncode == 2  # a missing artefact is an error, never a silent pass


def _artefacts(d: Path, version: str):
    files = {
        "windows-x86_64": f"OpenPhotoAgent_{version}_x64-setup.nsis.zip",
        "darwin-aarch64": "OpenPhotoAgent.app.tar.gz",
        "linux-x86_64": f"OpenPhotoAgent_{version}_amd64.AppImage.tar.gz",
    }
    for target, name in files.items():
        (d / name).write_bytes(b"x")
        (d / f"{name}.sig").write_text(f"sig-{target}\n", encoding="utf-8")
    return files


@pytest.mark.unit
@pytest.mark.tc("UPD-006")
@pytest.mark.parametrize("version,channel", [("0.1.0", "stable"), ("0.2.0-beta.1", "beta")])
def test_upd_006_manifest_matches_the_tauri_updater_schema(tmp_path, version, channel):
    files = _artefacts(tmp_path, version)
    out = tmp_path / ("latest.json" if channel == "stable" else "beta.json")
    r = _run(
        "tools/gen_updater_manifest.py",
        "--version",
        f"v{version}",
        "--dir",
        str(tmp_path),
        "--base-url",
        f"https://github.com/romanticamaj/openphotoagent/releases/download/v{version}",
        "--notes",
        "notes",
        "--pub-date",
        "2026-11-02T00:00:00Z",
        "--out",
        str(out),
    )
    assert r.returncode == 0, r.stderr
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["version"] == version  # Tauri compares SemVer without the leading v
    assert m["notes"] == "notes" and m["pub_date"] == "2026-11-02T00:00:00Z"
    assert set(m["platforms"]) == set(files)
    for target, name in files.items():
        p = m["platforms"][target]
        assert p["signature"] == f"sig-{target}"  # the .sig content, not a path
        assert p["url"] == f"https://github.com/romanticamaj/openphotoagent/releases/download/v{version}/{name}"


@pytest.mark.unit
@pytest.mark.tc("UPD-006")
def test_upd_006_unsigned_artefact_is_refused(tmp_path):
    _artefacts(tmp_path, "0.1.0")
    (tmp_path / "OpenPhotoAgent.app.tar.gz.sig").unlink()
    r = _run(
        "tools/gen_updater_manifest.py",
        "--version",
        "v0.1.0",
        "--dir",
        str(tmp_path),
        "--base-url",
        "https://x",
        "--notes",
        "",
        "--pub-date",
        "2026-11-02T00:00:00Z",
        "--out",
        str(tmp_path / "latest.json"),
    )
    assert r.returncode != 0 and "signature" in r.stderr.lower()
    assert not (tmp_path / "latest.json").exists()
