"""Parse 09a §8.2 (PF-001..066) into fixtures/golden/preflight_catalog.yaml (09a §8.1: "表格同時存成
preflight_catalog.yaml，CI 檢查兩者一致"). ``--check`` fails when the YAML is out of date."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "09a-test-cases-v0.1.md"
OUT = ROOT / "fixtures" / "golden" / "preflight_catalog.yaml"
FIX = re.compile(r"`([a-z_]+)(?::([^`]*))?`(?:（([^）]*)）)?")
DEPLOY = {"桌面": "desktop", "Docker": "docker", "Linux": "linux", "UI 精靈": None, "UI：查看清單": None}


def parse_fixes(cell: str) -> list[dict[str, object]]:
    main = re.split(r"[；;]", cell)[0]
    seq: list[dict[str, object]] = []
    for step in main.split("→"):
        alts = [m for m in FIX.finditer(step)]
        if not alts:
            continue
        options = []
        for m in alts:
            d: dict[str, object] = {"type": m.group(1)}
            if m.group(2):
                d["target"] = m.group(2).strip()
            note = (m.group(3) or "").strip()
            dep = DEPLOY.get(note, None) if note in DEPLOY else None
            if dep:
                d["deployment"] = dep
            options.append(d)
        seq.append(options[0] if len(options) == 1 else {"one_of": options})
    return seq


def parse() -> list[dict[str, object]]:
    rows = []
    for line in SRC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| PF-0") or int(line[5:8]) > 66:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        pid, code_cell, action, state, sev, fixes, ver = cells[:7]
        codes = re.findall(r"`([A-Z_]+)`", code_cell)
        rows.append(
            {
                "id": pid,
                "codes": codes,
                "action": action,
                "state": state,
                "severity": sev,
                "fixes": parse_fixes(fixes),
                "fixes_text": fixes,
                "version": ver,
            }
        )
    return rows


def main() -> int:
    data = yaml.safe_dump({"source": "docs/09a-test-cases-v0.1.md §8.2", "rows": parse()}, allow_unicode=True, sort_keys=False)
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != data:
            print("preflight_catalog.yaml is out of date: run tools/gen_preflight_catalog.py")
            return 1
        return 0
    OUT.write_text(data, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
