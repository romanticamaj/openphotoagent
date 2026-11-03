"""Vulnerability gate for releases (09b §9.2 SEC-SBOM-001).

    uv run python tools/vuln_scan.py             # runs pip-audit, pnpm audit, cargo audit; exit 1 on a gated finding

High and Critical findings fail unless ``security/vex.json`` (CycloneDX VEX) records why the product is not affected
— with a state, a justification and a human-readable detail. Moderate and lower are reported, not gated.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GATED = {"HIGH", "CRITICAL"}
NOT_AFFECTED = {"not_affected", "false_positive", "resolved"}


def validate_vex(vex: dict[str, Any]) -> list[str]:
    errors = []
    for v in vex.get("vulnerabilities") or []:
        a = v.get("analysis") or {}
        if not v.get("id") or not v.get("affects"):
            errors.append(f"{v.get('id')}: needs id and affects")
        if a.get("state") not in NOT_AFFECTED or not a.get("justification") or not a.get("detail"):
            errors.append(f"{v.get('id')}: analysis needs state, justification and detail")
    return errors


def _waived(f: dict[str, Any], vex: dict[str, Any]) -> bool:
    for v in vex.get("vulnerabilities") or []:
        if v.get("id") != f["id"] or validate_vex({"vulnerabilities": [v]}):
            continue
        if any(a.get("ref") == f["package"] for a in v.get("affects") or []):
            return True
    return False


def gate(findings: list[dict[str, Any]], vex: dict[str, Any]) -> list[dict[str, Any]]:
    """Findings that block the release."""
    return [f for f in findings if str(f.get("severity", "")).upper() in GATED and not _waived(f, vex)]


def _pip_audit() -> list[dict[str, Any]]:
    req = subprocess.run(
        ["uv", "export", "--frozen", "--no-hashes", "--no-emit-workspace"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout
    tmp = ROOT / ".tc" / "requirements-audit.txt"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_text(req)
    r = subprocess.run(
        ["uvx", "pip-audit", "-r", str(tmp), "-f", "json", "--progress-spinner", "off"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    data = json.loads(r.stdout)  # no JSON = the scan did not happen
    out = []
    for dep in data.get("dependencies", []):
        for v in dep.get("vulns", []):
            # pip-audit does not grade severity: treat every Python advisory as HIGH unless proven otherwise
            out.append(
                {
                    "id": v["id"],
                    "package": f"pkg:pypi/{dep['name'].lower()}@{dep['version']}",
                    "severity": "HIGH",
                    "fix": v.get("fix_versions"),
                }
            )
    return out


def _pnpm_audit() -> list[dict[str, Any]]:
    r = subprocess.run(["pnpm", "audit", "--json"], capture_output=True, text=True, cwd=ROOT)
    data = json.loads(r.stdout)
    out = []
    for adv in (data.get("advisories") or {}).values():
        for f in adv.get("findings") or [{}]:
            out.append(
                {
                    "id": adv.get("github_advisory_id") or str(adv.get("id")),
                    "package": f"pkg:npm/{adv['module_name']}@{f.get('version', '')}",
                    "severity": str(adv.get("severity", "")).upper(),
                }
            )
    return out


def _cargo_audit() -> list[dict[str, Any]]:
    out = []
    for lock in sorted(ROOT.glob("apps/**/Cargo.lock")):
        r = subprocess.run(["cargo", "audit", "--json", "-f", str(lock)], capture_output=True, text=True, cwd=ROOT)
        data = json.loads(r.stdout)
        for v in (data.get("vulnerabilities") or {}).get("list", []):
            adv, pkg = v["advisory"], v["package"]
            sev = "CRITICAL" if float((adv.get("cvss") or "0").split("/")[0] or 0) >= 9 else "HIGH"
            out.append({"id": adv["id"], "package": f"pkg:cargo/{pkg['name']}@{pkg['version']}", "severity": sev})
    return out


SCANNERS = {"pip-audit": "_pip_audit", "pnpm audit": "_pnpm_audit", "cargo audit": "_cargo_audit"}


def collect() -> tuple[list[dict[str, Any]], list[str]]:
    """(findings, scanners that could not run). A scanner that did not run is not a clean result."""
    findings: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for name, fn in SCANNERS.items():
        try:
            findings += globals()[fn]()
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as e:
            print(f"scanner unavailable: {name}: {type(e).__name__}: {str(e)[:200]}")
            unavailable.append(name)
    return findings, unavailable


def main() -> int:
    vex = json.loads((ROOT / "security/vex.json").read_text())
    errs = validate_vex(vex)
    findings, unavailable = collect()
    blocked = gate(findings, vex)
    for e in errs:
        print("VEX:", e)
    for f in findings:
        print(("BLOCK " if f in blocked else "note  ") + f"{f['severity']:9} {f['id']:24} {f['package']}")
    print(f"{len(findings)} findings, {len(blocked)} blocking, unavailable: {', '.join(unavailable) or 'none'}")
    return 1 if blocked or errs or unavailable else 0


if __name__ == "__main__":
    sys.exit(main())
