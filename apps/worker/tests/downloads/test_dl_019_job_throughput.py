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
from opagent_testing.hfserver import HfFile, HfServer
from opagent_worker.agent import WorkerAgent
from opagent_worker.downloads import CancelToken, DlFile, Downloader

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"
JOB = {"kind": "organize", "preset": "by-content-12", "target": {"root_alias": "sorted", "sub_path": ""}}
N = 1500


def _photos(root: Path, sub: str) -> None:
    (root / sub).mkdir(parents=True)
    for i in range(N):
        dst = root / sub / f"{i}.jpg"
        shutil.copyfile(FX / f"IMG_{i % 20 + 1:04d}.jpg", dst)
        with dst.open("ab") as fh:
            fh.write(f"{sub}{i}".encode())


def _timed_job(client, w, sub) -> tuple[float, dict]:
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": sub}).json()["id"]
    t0 = time.perf_counter()
    j = client.post("/api/v1/jobs", json={**JOB, "source_id": src}).json()
    while w.run_once() or w.process_items_once():
        pass
    secs = time.perf_counter() - t0
    return N / secs, client.get(f"/api/v1/jobs/{j['id']}").json()


@pytest.mark.integration
@pytest.mark.nightly
@pytest.mark.tc("DL-019")
def test_dl_019_download_does_not_slow_a_running_job(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "a")
    _photos(sandbox_tree.photos, "b")
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
    alone, ja = _timed_job(client, w, "a")
    assert ja["status"] == "awaiting_approval"

    srv = HfServer("org/big", [HfFile("model.gguf", 1024 * 1024 * 1024, seed=3)], rate_bps=100 * 1024 * 1024).start()
    try:
        dl = Downloader(tmp_path / "models", sources=[srv.url], reserve_floor=0, clock=AutoClock())
        f = srv.files[0]
        files = [DlFile(repo="org/big", revision="main", path=f.path, size=f.size, sha256=f.sha256)]
        done: dict = {}
        t = threading.Thread(target=lambda: done.update(r=asyncio.run(dl.download("op-dl", files, CancelToken()))))
        t.start()
        with_dl, jb = _timed_job(client, w, "b")
        overlapped = t.is_alive()  # the job finished while the download was still streaming
        t.join(120)
    finally:
        srv.stop()
    assert jb["status"] == "awaiting_approval"  # the job was never interrupted
    assert overlapped, "the job ended after the download: no overlap was measured"
    print(f"DL-019 job alone {alone:.0f} items/s, with download {with_dl:.0f} items/s")
    assert with_dl >= alone * 0.9
