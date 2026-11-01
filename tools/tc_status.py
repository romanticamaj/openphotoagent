#!/usr/bin/env python3
"""Generate docs/generated/coverage-status.md from 09a/09b + test results (docs/09 §1.1-6, §1.2).

Inputs:  docs/09a-test-cases-v0.1.md, docs/09b-test-cases-v0.2-plus.md
         .tc/*.json  ({ID: [{nodeid, outcome}]}) written by pytest-opagent-tc and the TS reporters
Output:  docs/generated/coverage-status.md

Usage:
  tools/tc_status.py                 # regenerate
  tools/tc_status.py --check         # fail if the committed file is stale
  tools/tc_status.py --gate=v0.1     # fail unless every v0.1 ID (layer != M) has a passing test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "docs/09a-test-cases-v0.1.md", ROOT / "docs/09b-test-cases-v0.2-plus.md"]
OUT = ROOT / "docs/generated/coverage-status.md"
ID_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}[a-z]?$")
VER_RE = re.compile(r"\bv(0\.\d|1\.0)\b")
CAP_RE = re.compile(r"#(\d+)")
LAYERS = set("UPCIEDVAFSXNM")


@dataclass
class Case:
    id: str
    title: str
    version: str
    layer: str
    caps: list[str]
    retired: bool
    doc: str


def parse_cases() -> list[Case]:
    cases: list[Case] = []
    seen: set[str] = set()
    for doc in DOCS:
        default = "0.1" if "09a" in doc.name else "0.2"
        section_ver = default
        for line in doc.read_text(encoding="utf-8").splitlines():
            if line.startswith("## ") or line.startswith("### "):
                m = VER_RE.search(line)
                if line.startswith("## "):
                    section_ver = m.group(1) if m else default
                continue
            if not line.startswith("| "):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not cells or not ID_RE.match(cells[0]) or cells[0] in seen:
                continue
            cid = cells[0]
            seen.add(cid)
            ver = section_ver
            for c in cells[1:]:
                m = re.fullmatch(r"v(0\.\d|1\.0)\b.*", c)
                if m:
                    ver = m.group(1)
            layer = ""
            for c in reversed(cells[1:]):
                parts = [p.strip() for p in c.split("/")]
                if parts and all(p in LAYERS for p in parts if p) and any(parts):
                    layer = parts[0]
                    break
            caps = sorted(set(CAP_RE.findall(cells[-1])) | set(CAP_RE.findall(cells[-2] if len(cells) > 1 else "")))
            retired = "廢止" in line
            cases.append(Case(cid, cells[1] if len(cells) > 1 else "", ver, layer, caps, retired, doc.name))
    return cases


def load_results() -> dict[str, list[dict[str, str]]]:
    res: dict[str, list[dict[str, str]]] = defaultdict(list)
    for f in sorted((ROOT / ".tc").glob("*.json")):
        for k, v in json.loads(f.read_text()).items():
            res[k].extend(v)
    return res


def status_of(runs: list[dict[str, str]]) -> str:
    if not runs:
        return "未開始"
    outs = Counter(r["outcome"] for r in runs)
    if outs.get("passed") and not (outs.get("failed") or outs.get("error")):
        return "綠"
    if outs.get("passed"):
        return "部分"
    return "紅"


def render(cases: list[Case], results: dict[str, list[dict[str, str]]]) -> str:
    by_ver: dict[str, Counter[str]] = defaultdict(Counter)
    by_mod: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    rows = []
    for c in cases:
        st = "廢止" if c.retired else status_of(results.get(c.id, []))
        by_ver[c.version][st] += 1
        mod = c.id.rsplit("-", 1)[0]
        by_mod[(c.version, mod)][st] += 1
        rows.append((c, st, len(results.get(c.id, []))))
    unknown = sorted(set(results) - {c.id for c in cases})
    lines = [
        "# 測試覆蓋狀態（自動產生，請勿手動編輯）",
        "",
        "> 由 `just tc-status`（`tools/tc_status.py`）依 09a／09b 與 `.tc/*.json` 測試結果產生。"
        "狀態：未開始／紅／綠／部分（同 ID 有通過也有失敗）。",
        "",
        "## 依版本",
        "",
        "| 版本 | 總數 | 綠 | 部分 | 紅 | 未開始 | 廢止 |",
        "|---|---|---|---|---|---|---|",
    ]
    for v in sorted(by_ver):
        c = by_ver[v]
        lines.append(
            f"| v{v} | {sum(c.values())} | {c['綠']} | {c['部分']} | {c['紅']} | {c['未開始']} | {c['廢止']} |"
        )
    lines += ["", "## 依模組", "", "| 版本 | 模組 | 總數 | 綠 | 未開始 | 其他 |", "|---|---|---|---|---|---|"]
    for v, mod in sorted(by_mod):
        c = by_mod[(v, mod)]
        tot = sum(c.values())
        lines.append(f"| v{v} | {mod} | {tot} | {c['綠']} | {c['未開始']} | {tot - c['綠'] - c['未開始']} |")
    lines += ["", "## 明細", "", "| ID | 版本 | 層 | # | 狀態 | 測試數 | 標題 |", "|---|---|---|---|---|---|---|"]
    for c, st, n in rows:
        caps = ",".join(f"#{x}" for x in c.caps)
        title = c.title.replace("|", "\\|")[:60]
        lines.append(f"| {c.id} | v{c.version} | {c.layer} | {caps} | {st} | {n} | {title} |")
    if unknown:
        lines += ["", "## 不在目錄中的 ID（請補到 09a／09b）", ""] + [f"- `{u}`" for u in unknown]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--gate")
    a = ap.parse_args()
    cases = parse_cases()
    results = load_results()
    text = render(cases, results)
    if a.gate:
        ver = a.gate.lstrip("v")
        missing = [
            c.id
            for c in cases
            if c.version == ver and not c.retired and c.layer != "M" and status_of(results.get(c.id, [])) != "綠"
        ]
        if missing:
            print(f"gate {a.gate}: {len(missing)} IDs without a passing test:\n  " + "\n  ".join(missing))
            return 1
        print(f"gate {a.gate}: OK")
        return 0
    if a.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("docs/generated/coverage-status.md is stale; run `just tc-status`")
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    c = Counter(status_of(results.get(x.id, [])) for x in cases)
    print(f"{len(cases)} cases; " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
