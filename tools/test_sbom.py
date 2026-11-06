"""SBOM and vulnerability gate (09b §9.2 SEC-SBOM-001)."""

import importlib.util
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    s = importlib.util.spec_from_file_location(name, ROOT / f"tools/{name}.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


sbom, vuln = _load("sbom"), _load("vuln_scan")


def expected():
    uv = {
        (p["name"], p["version"])
        for p in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]
        if "registry" in p.get("source", {})
    }
    cargo = {
        (p["name"], p["version"])
        for p in tomllib.loads((ROOT / "apps/desktop/supervisor/Cargo.lock").read_text(encoding="utf-8"))["package"]
        if p.get("source", "").startswith("registry+")
    }
    npm = set(yaml.safe_load((ROOT / "pnpm-lock.yaml").read_text(encoding="utf-8"))["packages"])
    return uv, cargo, npm


@pytest.mark.security
@pytest.mark.tc("SEC-SBOM-001")
def test_sec_sbom_001_covers_every_locked_package():
    bom = sbom.build()
    assert bom["bomFormat"] == "CycloneDX" and bom["specVersion"] == "1.5"
    comps = bom["components"]
    refs = [c["bom-ref"] for c in comps]
    assert len(refs) == len(set(refs))
    uv, cargo, npm = expected()
    got = {c["purl"] for c in comps}
    assert {f"pkg:pypi/{n.lower()}@{v}" for n, v in uv} <= got
    assert {f"pkg:cargo/{n}@{v}" for n, v in cargo} <= got
    assert len([c for c in comps if c["purl"].startswith("pkg:npm/")]) == len(npm)
    assert all(c["version"] and c["name"] for c in comps)
    assert not any(c["purl"].startswith("pkg:pypi/opagent") for c in comps)  # our own workspace members are not deps
    assert sbom.build() == bom  # deterministic: no timestamps or serials that change run to run


@pytest.mark.security
@pytest.mark.tc("SEC-SBOM-001")
def test_sec_sbom_001_gate_on_high_and_critical():
    findings = [
        {"id": "GHSA-1", "package": "pkg:pypi/a@1", "severity": "CRITICAL"},
        {"id": "GHSA-2", "package": "pkg:npm/b@2", "severity": "HIGH"},
        {"id": "GHSA-3", "package": "pkg:npm/c@3", "severity": "MODERATE"},
    ]
    assert [f["id"] for f in vuln.gate(findings, {"vulnerabilities": []})] == ["GHSA-1", "GHSA-2"]
    vex = {
        "vulnerabilities": [
            {
                "id": "GHSA-2",
                "analysis": {
                    "state": "not_affected",
                    "justification": "code_not_reachable",
                    "detail": "only used by the docs build",
                },
                "affects": [{"ref": "pkg:npm/b@2"}],
            }
        ]
    }
    assert [f["id"] for f in vuln.gate(findings, vex)] == ["GHSA-1"]
    bad_vex = {
        "vulnerabilities": [
            {"id": "GHSA-1", "analysis": {"state": "not_affected"}, "affects": [{"ref": "pkg:pypi/a@1"}]}
        ]
    }
    assert [f["id"] for f in vuln.gate(findings, bad_vex)] == ["GHSA-1", "GHSA-2"]  # a VEX entry needs a reason
    real = yaml.safe_load((ROOT / "security/vex.json").read_text(encoding="utf-8"))
    assert vuln.validate_vex(real) == []


@pytest.mark.security
@pytest.mark.tc("SEC-SBOM-001")
def test_sec_sbom_001_missing_scanner_is_not_a_pass(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("cargo-audit")

    monkeypatch.setattr(vuln.subprocess, "run", boom)
    findings, unavailable = vuln.collect()
    assert findings == [] and set(unavailable) == {"pip-audit", "pnpm audit", "cargo audit"}
