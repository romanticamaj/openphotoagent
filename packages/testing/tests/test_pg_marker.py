"""PG-015 (回歸): a test that uses the real PostgreSQL fixture is an integration test, whatever its own markers say.

CI's unit-py job has no PostgreSQL; 63 tests that took `embedded_pg` but were marked only `security`/`property`/none
were selected there and errored (the cloud dev box has pgvector, so `just test` hid it)."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FAST = "not integration and not e2e and not chaos and not perf and not release_gate and not nightly"
# files whose PG tests carried no integration marker when CI first ran them
SAMPLES = ("apps/api/tests/mcp/test_redteam.py", "apps/api/tests/mcp/test_mcp_audit.py")


def _collect(expr: str, *paths: str) -> list[str]:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly", "-m", expr, *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return [ln for ln in out.splitlines() if "::" in ln]


@pytest.mark.contract
@pytest.mark.tc("PG-015")
def test_pg_015_fast_selection_never_needs_postgres():
    assert _collect(FAST, *SAMPLES) == []
    integ = _collect("integration", *SAMPLES)
    assert any("test_sec_redteam_001" in n for n in integ) and any("test_aud_003" in n for n in integ)
