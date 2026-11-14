"""PERF-009 (回歸): Python perf tests (layer F, 09 §2.1) never share a process with the integration suite.

VIEW-009 ran at the end of `test-int` and timed a heap that ~300 earlier tests had filled: one generation-2 GC pass
(232 ms) landed in its 30-request window (ADR-0018). The integration selections must collect no perf test, and a
separate invocation — in `just` and in PR CI — must collect them, so they stay gated."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JUST = (ROOT / "justfile").read_text(encoding="utf-8")
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
PERF_TESTS = ("test_view_009_timeline_latency", "test_pf_075")


def _recipe(name: str) -> str:
    m = re.search(rf"^{re.escape(name)}:\n((?:[ \t]+.*\n?)+)", JUST, re.M)
    assert m, f"just recipe {name!r} not found"
    return m.group(1)


def _marker(line_source: str, results: str) -> str:
    m = re.search(rf"OPAGENT_TC_RESULTS={re.escape(results)} uv run pytest[^\n]*?-m \"([^\"]+)\"", line_source)
    assert m, f"no pytest -m invocation writing {results}"
    return m.group(1)


def _collect(expr: str) -> list[str]:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly", "-m", expr],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return [ln for ln in out.splitlines() if "::" in ln]


@pytest.mark.contract
@pytest.mark.tc("PERF-009")
def test_perf_009_integration_runs_collect_no_perf_test():
    for where, src in (("justfile test-int", _recipe("test-int")), ("ci.yml", CI)):
        expr = _marker(src, ".tc/py-int.json")
        leaked = _collect(f"({expr}) and perf")
        assert leaked == [], f"{where} would time perf tests inside the integration process: {leaked}"


@pytest.mark.contract
@pytest.mark.tc("PERF-009")
def test_perf_009_perf_tests_still_run_in_their_own_invocation():
    for where, src in (("justfile perf-api", _recipe("perf-api")), ("ci.yml", CI)):
        nodes = _collect(_marker(src, ".tc/py-perf.json"))
        for name in PERF_TESTS:
            assert any(name in n for n in nodes), f"{where} does not run {name}: {nodes}"
