"""DIST-001 (09b §7.6): package-manager manifests are generated from release metadata, reproducibly."""

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
META = {
    "version": "0.4.0",
    "release_notes_url": "https://github.com/romanticamaj/openphotoagent/releases/tag/v0.4.0",
    "assets": {
        "windows-x86_64": {"url": "https://example.invalid/OpenPhotoAgent_0.4.0_x64-setup.exe", "sha256": "a" * 64},
        "macos-arm64": {"url": "https://example.invalid/OpenPhotoAgent_0.4.0_aarch64.dmg", "sha256": "b" * 64},
        "linux-x86_64": {"url": "https://example.invalid/OpenPhotoAgent_0.4.0_amd64.AppImage", "sha256": "c" * 64},
    },
}


def gen():
    spec = importlib.util.spec_from_file_location("gen_dist", ROOT / "tools/gen_dist_manifests.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.unit
@pytest.mark.tc("DIST-001")
def test_dist_001_manifests(tmp_path):
    g = gen()
    a = g.generate(META)
    b = g.generate(META)
    assert a == b  # reproducible
    files = set(a)
    wid = "OpenPhotoAgent.OpenPhotoAgent"
    assert {
        f"winget/{wid}.yaml",
        f"winget/{wid}.installer.yaml",
        f"winget/{wid}.locale.en-US.yaml",
        "homebrew/openphotoagent.rb",
        "flathub/org.openphotoagent.OpenPhotoAgent.yml",
    } == files
    ver = yaml.safe_load(a[f"winget/{wid}.yaml"])
    assert ver == {
        "PackageIdentifier": wid,
        "PackageVersion": "0.4.0",
        "DefaultLocale": "en-US",
        "ManifestType": "version",
        "ManifestVersion": "1.6.0",
    }
    inst = yaml.safe_load(a[f"winget/{wid}.installer.yaml"])
    assert inst["Installers"][0]["InstallerSha256"] == "A" * 64 and inst["Installers"][0]["Architecture"] == "x64"
    loc = yaml.safe_load(a[f"winget/{wid}.locale.en-US.yaml"])
    assert loc["License"] == "Apache-2.0" and loc["ShortDescription"] and loc["PackageVersion"] == "0.4.0"
    rb = a["homebrew/openphotoagent.rb"]
    assert 'version "0.4.0"' in rb and f'sha256 "{"b" * 64}"' in rb and "depends_on arch: :arm64" in rb
    fl = yaml.safe_load(a["flathub/org.openphotoagent.OpenPhotoAgent.yml"])
    src = fl["modules"][0]["sources"][0]
    assert (
        src["sha256"] == "c" * 64
        and src["url"].endswith(".AppImage")
        and fl["app-id"] == "org.openphotoagent.OpenPhotoAgent"
    )
    for bad in (
        {**META, "version": "v0.4"},
        {**META, "assets": {**META["assets"], "macos-arm64": {"url": "x", "sha256": "zz"}}},
    ):
        with pytest.raises(ValueError):
            g.generate(bad)
