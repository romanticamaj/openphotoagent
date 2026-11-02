"""Package-manager manifests from release metadata (09b DIST-001; docs/research/distribution.md).

Input: a JSON file ``{version, release_notes_url, assets: {windows-x86_64|macos-arm64|linux-x86_64: {url, sha256}}}``
written by the release job. Output (deterministic): a winget multi-file manifest (1.6), a Homebrew cask, and a
Flathub manifest that repackages the AppImage. Submitting them needs accounts the maintainers own (BLOCKERS B-8).

Usage: ``python tools/gen_dist_manifests.py release.json OUT_DIR``
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

WINGET_ID = "OpenPhotoAgent.OpenPhotoAgent"
APP_ID = "org.openphotoagent.OpenPhotoAgent"
HOMEPAGE = "https://github.com/romanticamaj/openphotoagent"
DESC = "Local-first photo AI: caption, tag, organize and search your photos on your own computer."
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
SHA = re.compile(r"^[0-9a-f]{64}$")
NEEDED = ("windows-x86_64", "macos-arm64", "linux-x86_64")


def _check(meta: dict[str, Any]) -> None:
    if not SEMVER.match(str(meta.get("version", ""))):
        raise ValueError(f"version must be SemVer without a leading v: {meta.get('version')!r}")
    for k in NEEDED:
        a = (meta.get("assets") or {}).get(k) or {}
        if not str(a.get("url", "")).startswith("https://") or not SHA.match(str(a.get("sha256", ""))):
            raise ValueError(f"asset {k} needs an https url and a lowercase sha256")


def _yaml(doc: dict[str, Any], schema: str) -> str:
    head = f"# yaml-language-server: $schema={schema}\n"
    return head + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def generate(meta: dict[str, Any]) -> dict[str, str]:
    _check(meta)
    v = meta["version"]
    win, mac, lin = (meta["assets"][k] for k in NEEDED)
    base = "https://aka.ms/winget-manifest"
    out: dict[str, str] = {}
    out[f"winget/{WINGET_ID}.yaml"] = _yaml(
        {
            "PackageIdentifier": WINGET_ID,
            "PackageVersion": v,
            "DefaultLocale": "en-US",
            "ManifestType": "version",
            "ManifestVersion": "1.6.0",
        },
        f"{base}.version.1.6.0.schema.json",
    )
    out[f"winget/{WINGET_ID}.installer.yaml"] = _yaml(
        {
            "PackageIdentifier": WINGET_ID,
            "PackageVersion": v,
            "InstallerType": "nullsoft",
            "Scope": "user",
            "UpgradeBehavior": "install",
            "Installers": [
                {"Architecture": "x64", "InstallerUrl": win["url"], "InstallerSha256": win["sha256"].upper()}
            ],
            "ManifestType": "installer",
            "ManifestVersion": "1.6.0",
        },
        f"{base}.installer.1.6.0.schema.json",
    )
    out[f"winget/{WINGET_ID}.locale.en-US.yaml"] = _yaml(
        {
            "PackageIdentifier": WINGET_ID,
            "PackageVersion": v,
            "PackageLocale": "en-US",
            "Publisher": "OpenPhotoAgent contributors",
            "PackageName": "OpenPhotoAgent",
            "PackageUrl": HOMEPAGE,
            "License": "Apache-2.0",
            "ShortDescription": DESC,
            "ReleaseNotesUrl": meta.get("release_notes_url") or f"{HOMEPAGE}/releases",
            "ManifestType": "defaultLocale",
            "ManifestVersion": "1.6.0",
        },
        f"{base}.defaultLocale.1.6.0.schema.json",
    )
    out["homebrew/openphotoagent.rb"] = (
        'cask "openphotoagent" do\n'
        f'  version "{v}"\n'
        f'  sha256 "{mac["sha256"]}"\n\n'
        f'  url "{mac["url"]}"\n'
        '  name "OpenPhotoAgent"\n'
        f'  desc "{DESC}"\n'
        f'  homepage "{HOMEPAGE}"\n\n'
        "  depends_on arch: :arm64\n"
        '  depends_on macos: ">= :ventura"\n\n'
        '  app "OpenPhotoAgent.app"\n\n'
        '  zap trash: "~/Library/Application Support/org.openphotoagent.OpenPhotoAgent"\n'
        "end\n"
    )
    out[f"flathub/{APP_ID}.yml"] = yaml.safe_dump(
        {
            "app-id": APP_ID,
            "runtime": "org.gnome.Platform",
            "runtime-version": "47",
            "sdk": "org.gnome.Sdk",
            "command": "openphotoagent",
            "finish-args": [
                "--share=ipc",
                "--socket=wayland",
                "--socket=fallback-x11",
                "--device=dri",
                "--share=network",
                "--filesystem=xdg-pictures",
            ],
            "modules": [
                {
                    "name": "openphotoagent",
                    "buildsystem": "simple",
                    "build-commands": [
                        "chmod +x OpenPhotoAgent.AppImage",
                        "./OpenPhotoAgent.AppImage --appimage-extract",
                        "cp -r squashfs-root /app/openphotoagent",
                        "ln -s /app/openphotoagent/AppRun /app/bin/openphotoagent",
                    ],
                    "sources": [
                        {
                            "type": "file",
                            "url": lin["url"],
                            "sha256": lin["sha256"],
                            "dest-filename": "OpenPhotoAgent.AppImage",
                        }
                    ],
                }
            ],
        },
        sort_keys=False,
    )
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    files = generate(json.loads(Path(argv[0]).read_text(encoding="utf-8")))
    for rel, body in files.items():
        p = Path(argv[1]) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    print("\n".join(sorted(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
