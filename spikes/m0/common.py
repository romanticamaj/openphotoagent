"""Shared helpers for M0 spike experiments (docs/09 §6).

Every experiment writes ``spikes/m0/results/<id>.json`` with the schema
``{id, machine, metrics{}, pass, notes}`` plus ``status``:

* ``measured``          – metrics are real measurements from this machine
* ``partial``           – some metrics measured here; the rest need other hardware
* ``pending-hardware``  – nothing measured; must be run on the listed hardware
* ``blocked``           – cannot run (e.g. network policy); reason in notes

``pass`` is ``true``/``false`` only when the pass criterion was fully evaluated,
otherwise ``null``. Never write estimated numbers into ``metrics`` – put
estimates under ``metrics["estimates"]`` with an explicit label.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).resolve().parent / "results"
VALID_STATUS = {"measured", "partial", "pending-hardware", "blocked"}


def machine_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "hostname_label": os.environ.get("OPAGENT_M0_MACHINE", "unlabeled"),
        "os": platform.system(),
        "os_release": platform.release(),
        "arch": platform.machine(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
    }
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    info["mem_total_kb"] = int(line.split()[1])
    except OSError:
        pass
    return info


def percentile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("no values")
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "min": min(values),
        "p50": statistics.median(values),
        "p95": percentile(values, 0.95),
        "max": max(values),
        "mean": statistics.fmean(values),
    }


def write_result(
    exp_id: str,
    *,
    status: str,
    metrics: dict[str, Any],
    passed: bool | None,
    notes: str,
    machine: dict[str, Any] | None = None,
) -> Path:
    if status not in VALID_STATUS:
        raise ValueError(f"bad status {status}")
    if status != "measured" and passed is not None and status != "partial":
        raise ValueError("pass must be null unless measured/partial")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{exp_id}.json"
    doc = {
        "id": exp_id,
        "status": status,
        "machine": machine if machine is not None else machine_info(),
        "metrics": metrics,
        "pass": passed,
        "notes": notes,
    }
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)
