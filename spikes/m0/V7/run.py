"""M0-V7 — ETA 80 % interval coverage (docs/09 §6, 06 §5.5.5, 11 V-7).

Input: an export of the `eta_calibration` table from a reference machine after
running 3 task types × 3 times (`opagent ops export-calibration > cal.json`,
available from slice 6). Each row: {op_type, predicted_p10_s, predicted_p90_s, actual_s}.
Pass: coverage of [p10, p90] ≥ 70 % (v0.1 threshold).

    uv run python spikes/m0/V7/run.py --calibration cal.json --machine rtx5070ti-win11
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import write_result  # noqa: E402


def coverage(rows: list[dict[str, float]]) -> float:
    hit = sum(1 for r in rows if r["predicted_p10_s"] <= r["actual_s"] <= r["predicted_p90_s"])
    return hit / len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibration", type=Path, required=True)
    ap.add_argument("--machine", default="unlabeled")
    a = ap.parse_args()
    rows = json.loads(a.calibration.read_text())
    by: dict[str, list[dict[str, float]]] = defaultdict(list)
    for r in rows:
        by[r["op_type"]].append(r)
    m = {"n": len(rows), "coverage_80": coverage(rows), "by_type": {k: coverage(v) for k, v in by.items()}}
    write_result("M0-V7", status="measured", metrics=m, passed=m["coverage_80"] >= 0.70, notes=f"machine={a.machine}")
    print(m)


if __name__ == "__main__":
    main()
