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


def load_results(dirs: list[Path] | None = None, inventory: bool = False) -> dict[str, list[dict[str, str]]]:
    """Results ({ID: [{nodeid, outcome}]}) from .tc/ or the given directories (searched recursively, so CI's
    per-artifact folders never overwrite each other). Inventory files (inventory-*.json) are kept apart."""
    res: dict[str, list[dict[str, str]]] = defaultdict(list)
    files = (
        sorted((ROOT / ".tc").glob("*.json")) if dirs is None else sorted(f for d in dirs for f in d.rglob("*.json"))
    )
    for f in files:
        if f.name.startswith("inventory-") != inventory:
            continue
        for k, v in json.loads(f.read_text(encoding="utf-8")).items():
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
    statuses = {c.id: "廢止" if c.retired else status_of(results.get(c.id, [])) for c in cases}
    counts = {c.id: len(results.get(c.id, [])) for c in cases}
    return render_status(cases, statuses, counts, sorted(set(results) - {c.id for c in cases}))


def render_status(
    cases: list[Case], statuses: dict[str, str], counts: dict[str, int], unknown: list[str] | None = None
) -> str:
    """The file from per-ID statuses and test counts (the summary tables are derived, never hand-written)."""
    by_ver: dict[str, Counter[str]] = defaultdict(Counter)
    by_mod: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    rows = []
    for c in cases:
        st = statuses.get(c.id, "未開始")
        by_ver[c.version][st] += 1
        mod = c.id.rsplit("-", 1)[0]
        by_mod[(c.version, mod)][st] += 1
        rows.append((c, st, counts.get(c.id, 0)))
    unknown = unknown or []
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


def verify(
    cases: list[Case],
    committed_text: str,
    pr: dict[str, list[dict[str, str]]],
    inventory: dict[str, list[dict[str, str]]],
    nightly: dict[str, list[dict[str, str]]] | None = None,
    strict_nightly: bool = False,
) -> tuple[list[str], list[str]]:
    """ADR-0021. The committed file may hold evidence PR CI cannot produce (nightly, visual, perf), and CI may see
    results the dev box cannot (other OSes, PG 17) — so the check is consistency, not equality:
      1. every row and summary renders from 09a/09b plus the committed per-ID status (no hand edits, no drift);
      2. no ID outside 09a/09b, in CI results or the inventory;
      3. an ID CI ran is never greener in the file than in CI (CI greener → warning only);
      4. an ID CI did not run is 未開始 unless the inventory has a test for it, and only non-PR-tier tests;
         a PR-tier test with no CI result fails;
      5. with nightly evidence, a non-PR claim greener than the nightly result warns (fails with strict_nightly)."""
    errors: list[str] = []
    warnings: list[str] = []
    known = {c.id for c in cases}
    claimed: dict[str, str] = {}
    counts: dict[str, int] = {}
    for line in committed_text.splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(cells) == 7 and ID_RE.match(cells[0]) and cells[1].startswith("v"):
            claimed[cells[0]] = cells[4]
            counts[cells[0]] = int(cells[5]) if cells[5].isdigit() else 0
    if render_status(cases, claimed, counts) != committed_text:
        errors.append("rule 1: the file does not render from 09a/09b and its own statuses (stale rows or a hand edit)")
    for i in sorted((set(pr) | set(inventory)) - known):
        errors.append(f"rule 2: {i} is not in 09a/09b")
    order = {"紅": 0, "部分": 1, "綠": 2}
    for c in cases:
        if c.retired:
            continue
        mine = claimed.get(c.id, "未開始")
        if c.id in pr:
            ci = status_of(pr[c.id])
            if ci == "紅" and mine != "紅":
                errors.append(f"rule 3: {c.id} is red in CI but {mine} in the file")
            elif ci == "部分" and mine not in ("部分", "紅"):
                errors.append(f"rule 3: {c.id} is partial in CI but {mine} in the file")
            elif ci == "綠" and mine != "綠":
                warnings.append(f"rule 3: {c.id} is green in CI but {mine} in the file (refresh it)")
            continue
        tiers = {t.get("tier", "pr") for t in inventory.get(c.id, [])}
        if "pr" in tiers:
            errors.append(f"rule 4: {c.id} has a PR-tier test but no result from this CI run")
            continue
        if mine == "未開始":
            continue
        if not tiers:
            errors.append(f"rule 4: {c.id} is {mine} in the file but no test for it exists")
            continue
        if nightly and c.id in nightly and order.get(mine, -1) > order[status_of(nightly[c.id])]:
            msg = f"rule 5: {c.id} is {mine} in the file but {status_of(nightly[c.id])} in the nightly run"
            (errors if strict_nightly else warnings).append(msg)
    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="the committed file renders from the local .tc/ results")
    ap.add_argument("--gate")
    ap.add_argument("--verify", action="store_true", help="CI: the committed file is consistent with CI (ADR-0021)")
    ap.add_argument("--pr-results", type=Path, action="append", default=[])
    ap.add_argument("--inventory", type=Path, action="append", default=[])
    ap.add_argument("--nightly-results", type=Path, action="append", default=[])
    ap.add_argument("--strict-nightly", action="store_true")
    a = ap.parse_args()
    cases = parse_cases()
    if a.verify:
        errors, warnings = verify(
            cases,
            OUT.read_text(encoding="utf-8") if OUT.exists() else "",
            load_results(a.pr_results),
            load_results(a.inventory + a.pr_results, inventory=True),
            load_results(a.nightly_results) if a.nightly_results else None,
            a.strict_nightly,
        )
        for w in warnings:
            print(f"::warning::{w}")
        for e in errors:
            print(f"::error::{e}")
        print(f"verify: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1 if errors else 0
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
