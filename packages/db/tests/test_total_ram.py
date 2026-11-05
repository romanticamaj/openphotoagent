"""PG-021 (回歸): the desktop profile sizes shared_buffers from the machine's real RAM (PG-003).

``os.sysconf`` does not exist on Windows; the old code caught the AttributeError and returned a made-up 8.0 GB, so
every Windows desktop got the small-laptop pool whatever it had installed."""

import subprocess

import pytest
from opagent_db.embedded import _total_ram_gb


@pytest.mark.unit
@pytest.mark.os_windows
@pytest.mark.tc("PG-021")
def test_pg_021_total_ram_is_the_machines_on_windows():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    assert _total_ram_gb() == pytest.approx(int(out) / 2**30, rel=0.01)
