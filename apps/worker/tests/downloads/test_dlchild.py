"""Download child process (ADR-0007): progress over a pipe, and no orphan when the worker dies (DESK-006)."""

import time

import pytest
from opagent_testing.hfserver import HfFile, HfServer
from opagent_worker.dlchild import ChildDownload

REPO = "org/model"


@pytest.mark.integration
@pytest.mark.tc("DESK-006", "DL-014")
def test_desk_006_download_child_leaves_with_the_worker_and_keeps_parts(tmp_path):
    srv = HfServer(REPO, [HfFile("big.bin", 40 * 1024 * 1024, seed=1)], rate_bps=2 * 1024 * 1024).start()
    try:
        f = srv.files[0]
        files = [{"repo": REPO, "revision": "main", "path": f.path, "size": f.size, "sha256": f.sha256}]
        c = ChildDownload(tmp_path / "models", [srv.url], "op1", files)
        seen = None
        deadline = time.monotonic() + 60  # noqa: TID251 - waiting on a real process
        while seen is None and time.monotonic() < deadline:  # noqa: TID251
            ev = c.next_event()
            if ev and ev[0] == "progress" and ev[1]["done_bytes"] > 0:
                seen = ev[1]
        assert seen is not None and seen["total_bytes"] == f.size  # progress arrives from the child
        c._ctl_w.close()  # what the OS does to the pipe when the worker is killed
        t0 = time.monotonic()  # noqa: TID251
        c.proc.join(10)
        assert not c.proc.is_alive() and time.monotonic() - t0 <= 5  # noqa: TID251
        assert list((tmp_path / "models").rglob("*.part")), "partial data is kept for the next worker to resume"
    finally:
        srv.stop()
