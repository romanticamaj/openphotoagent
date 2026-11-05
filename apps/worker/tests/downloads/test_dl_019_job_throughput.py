"""DL-019 (nightly): a 1 GB model download at 100 MB/s while a Job runs — Job throughput drops < 10 %, no stop.

Production path (ADR-0007): the download is started with the install API and leased by the worker's long-running
loop (``background_downloads``); the model host is a separate process, as a remote host would be. FakeBackend is
GIL-bound on purpose — harsher than llama-server or ONNX Runtime."""

import json
import multiprocessing as mp
import shutil
import statistics
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_inference.fake import FakeBackend
from opagent_registry.loader import BUILTIN_DIR
from opagent_testing.hfserver import serve_in_child
from opagent_worker.agent import WorkerAgent

ROOT = Path(__file__).resolve().parents[4]
FX = ROOT / "fixtures/photos/basic"
JOB = {"kind": "organize", "preset": "by-content-12", "target": {"root_alias": "sorted", "sub_path": ""}}
EMB = "siglip2-so400m-384"
GB = 1024 * 1024 * 1024
FILES = [("onnx/vision_model_int8.onnx", GB, 1), ("onnx/text_model_int8.onnx", 200_000, 2), ("tokenizer.json", 1_000, 3)]


def _photos(root: Path, sub: str, n: int) -> None:
    (root / sub).mkdir(parents=True)
    for i in range(n):
        dst = root / sub / f"{i}.jpg"
        shutil.copyfile(FX / f"IMG_{i % 20 + 1:04d}.jpg", dst)
        with dst.open("ab") as fh:
            fh.write(f"{sub}{i}".encode())


def _loop_once(w) -> bool:
    return bool(w.run_once(background_downloads=True) or w.process_items_once())


def _timed_job(client, w, sub, n) -> tuple[float, dict]:
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": sub}).json()["id"]
    t0 = time.perf_counter()
    j = client.post("/api/v1/jobs", json={**JOB, "source_id": src}).json()
    while client.get(f"/api/v1/jobs/{j['id']}").json()["status"] not in ("awaiting_approval", "failed", "completed"):
        _loop_once(w)
    secs = time.perf_counter() - t0
    return n / secs, client.get(f"/api/v1/jobs/{j['id']}").json()


@pytest.mark.integration
@pytest.mark.nightly
@pytest.mark.tc("DL-019")
def test_dl_019_download_does_not_slow_a_running_job(embedded_pg, sandbox_tree, tmp_path):
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    host = ctx.Process(
        target=serve_in_child,
        args=(child, "onnx-community/siglip2-so400m-patch14-384-ONNX", FILES, 100 * 1024 * 1024),
        daemon=True,
    )
    host.start()
    try:
        assert parent.poll(300), "model host did not start"
        url, served = parent.recv()
        reg = tmp_path / "registry"
        shutil.copytree(BUILTIN_DIR, reg)
        pins = [
            {
                "model": EMB,
                "variant": "onnx-int8",
                "revision": "0123456789abcdef0123456789abcdef01234567",
                "files": [{"path": p, "size": n, "sha256": h, "lfs_oid": h} for p, n, h in served],
            }
        ]
        (reg / "pins.lock").write_text(json.dumps({"schema_version": 1, "pins": pins}), encoding="utf-8")
        client = TestClient(create_app(dsn=embedded_pg.dsn, registry_dir=reg))
        w = WorkerAgent(
            client,
            roots=sandbox_tree.roots,
            home=tmp_path / "w",
            backend=FakeBackend(),
            embedder=FakeBackend(),
            worker_id="w1",
            hw_profile="rtx5070ti-win11",
            models_root=tmp_path / "models",
            download_sources=[url],
        )
        w.register()
        _photos(sandbox_tree.photos, "a", 600)
        rate, ja = _timed_job(client, w, "a", 600)  # sizing run only
        assert ja["status"] == "awaiting_approval"
        # compared jobs are equal in size (fixed per-job overhead cancels) and sized to ~2.5 s of work, so two of
        # them fit inside the ~10 s download; alone / with-download pairs, medians compared
        nb = max(50, int(rate * 2.5))
        for s in ("b0", "b1", "c0", "c1"):
            _photos(sandbox_tree.photos, s, nb)
        alone = [_timed_job(client, w, "b0", nb)[0]]
        op = client.post("/api/v1/workers/w1/installs", json={"variant": f"{EMB}@onnx-int8"}).json()["operation_id"]
        while client.get(f"/api/v1/operations/{op}").json()["status"] in ("queued",):  # leased by the loop
            _loop_once(w)
        with_dl = [_timed_job(client, w, "b1", nb)[0]]
        with_dl.append(_timed_job(client, w, "c1", nb)[0])
        mid = client.get(f"/api/v1/operations/{op}").json()
        overlapped = mid["status"] in ("running", "waiting")
        while client.get(f"/api/v1/operations/{op}").json()["status"] not in ("succeeded", "failed", "cancelled"):
            _loop_once(w)
        alone.append(_timed_job(client, w, "c0", nb)[0])
        final = client.get(f"/api/v1/operations/{op}").json()
    finally:
        parent.send("stop")
        host.join(30)
        shutil.rmtree(tmp_path / "models", ignore_errors=True)  # 1 GB: pytest keeps recent tmp_path folders
    assert overlapped, f"both measured jobs must run while the download streams: {mid}"
    assert final["status"] == "succeeded", final
    a, d = statistics.median(alone), statistics.median(with_dl)
    print(f"DL-019 job alone {alone} → {a:.0f} items/s, with download {with_dl} → {d:.0f} items/s")
    assert d >= a * 0.9
