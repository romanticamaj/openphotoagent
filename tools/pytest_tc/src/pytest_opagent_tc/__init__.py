"""pytest-opagent-tc — links tests to 09a/09b test-case IDs (docs/09 §1.2).

Usage: ``@pytest.mark.tc("FS-012", "FS-013")``. After the run, results are
written to ``$OPAGENT_TC_RESULTS`` (default ``.tc/py.json``) as
``{"FS-012": [{"nodeid": ..., "outcome": "passed"}], ...}`` and merged by
``tools/tc_status.py``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

ID_RE = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)*-\d{3}[a-z]?$")
_results: dict[str, list[dict[str, str]]] = {}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "tc(*ids): link a test to 09a/09b test case IDs")


def _ids(item: pytest.Item) -> list[str]:
    out: list[str] = []
    for m in item.iter_markers(name="tc"):
        for a in m.args:
            if not isinstance(a, str) or not ID_RE.match(a):
                raise pytest.UsageError(f"{item.nodeid}: bad tc id {a!r}")
            out.append(a)
    return out


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> Any:
    outcome = yield
    rep = outcome.get_result()
    ids = _ids(item)
    if not ids:
        return
    if rep.when == "call" or (rep.when == "setup" and rep.outcome != "passed"):
        state = rep.outcome
        if rep.when == "setup" and rep.skipped:
            state = "skipped"
        elif rep.when == "setup":
            state = "error"
        for i in ids:
            _results.setdefault(i, []).append({"nodeid": item.nodeid, "outcome": state})


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _results or hasattr(session.config, "workerinput"):
        return
    path = Path(os.environ.get("OPAGENT_TC_RESULTS", ".tc/py.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, list[dict[str, str]]] = {}
    if path.exists() and os.environ.get("OPAGENT_TC_APPEND"):
        existing = json.loads(path.read_text(encoding="utf-8"))
    for k, v in _results.items():
        existing.setdefault(k, []).extend(v)
    path.write_text(json.dumps(existing, indent=1, sort_keys=True), encoding="utf-8")
