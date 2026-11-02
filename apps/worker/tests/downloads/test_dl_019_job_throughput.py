"""DL-019 (nightly): a 1 GB model download at 100 MB/s while a Job runs — Job throughput drops < 10 %, no stop."""

import asyncio
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_inference.fake import FakeBackend
from opagent_testing.clock import AutoClock
from opagent_testing.hfserver import serve_in_child
from opagent_worker.agent import WorkerAgent
from opagent_worker.downloads import CancelToken, DlFile, Downloader

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"
JOB = {"kind": "organize", "preset": "by-content-12", "target": {"root_alias": "sorted", "sub_path": ""}}
N = 1500


def _photos(root: Path, sub: str, n: int = N) -> None:
    (root / sub).mkdir(parents=True)
    for i in range(n):
        dst = root / sub / f"{i}.jpg"
        shutil.copyfile(FX / f"IMG_{i % 20 + 1:04d}.jpg", dst)
        with dst.open("ab") as fh:
            fh.write(f"{sub}{i}".encode())


def _timed_job(client, w, sub, n: int = N) -> tuple[float, dict]:
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": sub}).json()["id"]
    t0 = time.perf_counter()
    j = client.post("/api/v1/jobs", json={**JOB, "source_id": src}).json()
    while w.run_once() or w.process_items_once():
        pass
    secs = time.perf_counter() - t0
    return n / secs, client.get(f"/api/v1/jobs/{j['id']}").json()


@pytest.mark.integration
@pytest.mark.nightly
@pytest.mark.tc("DL-019")
def test_dl_019_download_does_not_slow_a_running_job(embedded_pg, sandbox_tree, tmp_path):
    import multiprocessing as mp

    _photos(sandbox_tree.photos, "a")
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    w = WorkerAgent(
        client,
        roots=sandbox_tree.roots,
        home=tmp_path / "w",
        backend=FakeBackend(),
        embedder=FakeBackend(),
        worker_id="w1",
    )
    w.register()
    rate, ja = _timed_job(client, w, "a")  # sizing run only
    assert ja["status"] == "awaiting_approval"
    # the compared jobs are sized to about 5 s of work so the second runs entirely inside the ~10 s download
    # (1 GB at 100 MB/s); both have the same size, so fixed per-job overhead cancels out
    nb = max(50, int(rate * 5))
    _photos(sandbox_tree.photos, "b0", nb)
    _photos(sandbox_tree.photos, "b", nb)
    alone, j0 = _timed_job(client, w, "b0", nb)
    assert j0["status"] == "awaiting_approval"

    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    gb = 1024 * 1024 * 1024
    host = ctx.Process(
        target=serve_in_child, args=(child, "org/big", "model.gguf", gb, 3, 100 * 1024 * 1024), daemon=True
    )
    host.start()
    try:
        assert parent.poll(120), "model host did not start"
        url, path, size, sha = parent.recv()
        # throughput is measured here, not the disk precheck (DL-010/011 cover that); the 1 GB still has to fit
        dl = Downloader(
            tmp_path / "models", sources=[url], reserve_floor=0, clock=AutoClock(), disk_free=lambda _p: 10**12
        )
        files = [DlFile(repo="org/big", revision="main", path=path, size=size, sha256=sha)]
        done: dict = {}
        t = threading.Thread(target=lambda: done.update(r=asyncio.run(dl.download("op-dl", files, CancelToken()))))
        t.start()
        with_dl, jb = _timed_job(client, w, "b", nb)
        overlapped = t.is_alive()  # the job finished while the download was still streaming
        t.join(120)
    finally:
        parent.send("stop")
        host.join(30)
        shutil.rmtree(tmp_path / "models", ignore_errors=True)  # 1 GB: pytest keeps recent tmp_path folders
    assert jb["status"] == "awaiting_approval"  # the job was never interrupted
    assert overlapped, "the job ended after the download: no overlap was measured"
    print(f"DL-019 job alone {alone:.0f} items/s, with download {with_dl:.0f} items/s")
    assert with_dl >= alone * 0.9
