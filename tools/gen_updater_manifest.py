"""Tauri updater manifest (08 §5.3, UPD-006): ``latest.json`` (stable) or ``beta.json`` from release artefacts.

Each updater artefact must sit next to its minisign ``.sig`` (written by ``tauri build`` with
``createUpdaterArtifacts``); an unsigned artefact aborts — the app would refuse it anyway (UPD-002).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# updater artefact name → Tauri target key
PATTERNS = {
    "windows-x86_64": re.compile(r"_x64-setup\.nsis\.zip$|_x64-setup\.exe$"),
    "darwin-aarch64": re.compile(r"\.app\.tar\.gz$"),
    "linux-x86_64": re.compile(r"\.AppImage\.tar\.gz$|\.AppImage$"),
}


def build(
    version: str, folder: Path, base_url: str, notes: str, pub_date: str, channel: str | None = None
) -> dict[str, object]:
    # no channel given: the version decides (a pre-release is beta); asked for stable, a pre-release is refused
    if channel == "stable" and "-" in version.lstrip("v"):
        # the stable channel only ever carries releases (UPD-010); a beta goes to beta.json
        raise SystemExit(f"{version} is a pre-release and cannot be published to the stable channel")
    platforms: dict[str, dict[str, str]] = {}
    for f in sorted(folder.iterdir()):
        if f.suffix == ".sig" or not f.is_file():
            continue
        for target, pat in PATTERNS.items():
            if pat.search(f.name) and target not in platforms:
                sig = f.with_name(f.name + ".sig")
                if not sig.is_file():
                    raise SystemExit(f"missing signature for {f.name} (expected {sig.name})")
                platforms[target] = {
                    "signature": sig.read_text(encoding="utf-8").strip(),
                    "url": f"{base_url.rstrip('/')}/{f.name}",
                }
    if not platforms:
        raise SystemExit("no updater artefacts found")
    return {"version": version.lstrip("v"), "notes": notes, "pub_date": pub_date, "platforms": platforms}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--notes", default="")
    ap.add_argument("--pub-date", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--channel", choices=["stable", "beta"])
    a = ap.parse_args()
    try:
        manifest = build(a.version, Path(a.dir), a.base_url, a.notes, a.pub_date, channel=a.channel)
    except SystemExit as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    Path(a.out).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
