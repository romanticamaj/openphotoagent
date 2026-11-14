"""CycloneDX 1.5 SBOM from the lockfiles (09b §9.2 SEC-SBOM-001; 08 §8 supply chain).

    uv run python tools/sbom.py > sbom.cdx.json

Python from ``uv.lock`` (registry packages only: workspace members are this product), JavaScript from
``pnpm-lock.yaml``, Rust from every ``Cargo.lock`` in the repo (the Tauri shell's lock is generated in CI before
this runs). Output is deterministic — no timestamp or random serial — so it can be diffed and attested.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _pypi() -> list[dict[str, Any]]:
    out = []
    for p in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]:
        if "registry" not in p.get("source", {}):
            continue
        name = p["name"].lower()
        out.append(
            {"type": "library", "name": name, "version": p["version"], "purl": f"pkg:pypi/{name}@{p['version']}"}
        )
    return out


def _npm() -> list[dict[str, Any]]:
    out = []
    for key in yaml.safe_load((ROOT / "pnpm-lock.yaml").read_text(encoding="utf-8")).get("packages") or {}:
        name, _, version = key.rpartition("@")
        purl_name = name.replace("@", "%40", 1) if name.startswith("@") else name
        out.append({"type": "library", "name": name, "version": version, "purl": f"pkg:npm/{purl_name}@{version}"})
    return out


def _cargo() -> list[dict[str, Any]]:
    out = []
    for lock in sorted(ROOT.glob("apps/**/Cargo.lock")):
        if "target" in lock.parts or "node_modules" in lock.parts:
            continue
        for p in tomllib.loads(lock.read_text(encoding="utf-8"))["package"]:
            if not str(p.get("source", "")).startswith("registry+"):
                continue
            out.append(
                {
                    "type": "library",
                    "name": p["name"],
                    "version": p["version"],
                    "purl": f"pkg:cargo/{p['name']}@{p['version']}",
                }
            )
    return out


def build() -> dict[str, Any]:
    seen: dict[str, dict[str, Any]] = {}
    for c in [*_pypi(), *_npm(), *_cargo()]:
        seen.setdefault(c["purl"], {**c, "bom-ref": c["purl"]})
    version = (ROOT / "packages/core/src/opagent_core/version.py").read_text(encoding="utf-8").split('"')[1]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {"component": {"type": "application", "name": "openphotoagent", "version": version}},
        "components": [seen[k] for k in sorted(seen)],
    }


if __name__ == "__main__":
    json.dump(build(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
