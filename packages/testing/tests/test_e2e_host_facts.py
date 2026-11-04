"""VIS-005 (回歸): what the E2E stack shows must not depend on the machine running it.

The Doctor page's "model folder space" check read the host's real free disk; once the dev container dropped
below 20 GB, the page grew a warning and VIS-002's doctor snapshots changed by ~1 % (seen on a local gate run)."""

import shutil
from pathlib import Path

import pytest

GB = 1024**3


@pytest.mark.unit
@pytest.mark.tc("VIS-005")
def test_vis_005_e2e_doctor_report_ignores_host_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from opagent_testing import e2e_stack
    from opagent_worker.agent import WorkerAgent

    reports = []
    for free in (GB // 2, 5 * GB, 2000 * GB):
        monkeypatch.setattr(shutil, "disk_usage", lambda _p, f=free: shutil._ntuple_diskusage(4000 * GB, 0, f))
        home = tmp_path / str(free)
        (home / "photos").mkdir(parents=True)
        w = WorkerAgent(
            None, [{"alias": "photos", "path": str(home / "photos")}], home / "w", hw_profile="rtx5070ti-win11"
        )
        e2e_stack.pin_host_facts(w)
        timeless = ("duration_ms", "checked_at", "expires_at")  # when it ran, not what it found
        reports.append([{k: v for k, v in r.items() if k not in timeless} for r in w.doctor_report()["doctor_results"]])
    assert reports[0] == reports[1] == reports[2]
    disk = [r for r in reports[0] if r["check_id"] == "disk.models"]
    assert disk and disk[0]["status"] == "ok", disk
