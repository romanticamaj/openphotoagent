import asyncio
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pytest
from opagent_testing.clock import AutoClock
from opagent_testing.hfserver import HfFile, HfServer
from opagent_worker.downloads import CancelToken, DlFile, Downloader, DownloadError

REPO = "org/model"
MB = 1024 * 1024


class Disk:
    def __init__(self, free: int) -> None:
        self.free = free

    def __call__(self, path: str) -> int:
        return self.free


def files_for(srv: HfServer) -> list[DlFile]:
    return [DlFile(repo=REPO, revision="main", path=f.path, size=f.size, sha256=f.sha256) for f in srv.files]


def mk(srv, root, **kw):
    kw.setdefault("disk_free", Disk(10**12))
    kw.setdefault("clock", AutoClock())
    return Downloader(Path(root), sources=[srv.url], reserve_floor=0, **kw)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def server():
    started = []

    def make(files, scenario="ok", rate_bps=None):
        s = HfServer(REPO, files, scenario=scenario, rate_bps=rate_bps).start()
        started.append(s)
        return s

    yield make
    for s in started:
        s.stop()


@pytest.mark.integration
@pytest.mark.tc("DL-001")
def test_dl_001_exact_total_known_up_front_small_files_first(server, tmp_path):
    srv = server([HfFile("model.gguf", 3 * MB), HfFile("config.json", 300), HfFile("tokenizer.json", 2000)])
    events = []
    d = mk(srv, tmp_path, on_progress=events.append)
    res = run(d.download("op1", files_for(srv), CancelToken()))
    assert res.status == "done"
    assert events[0]["total_bytes"] == 3 * MB + 2300 and events[0]["done_bytes"] == 0
    cdn = [r["path"] for r in srv.gets() if r["path"].startswith("/" + REPO)]
    assert [p.rsplit("/", 1)[1] for p in cdn] == ["config.json", "tokenizer.json", "model.gguf"]


@pytest.mark.integration
@pytest.mark.tc("DL-002")
def test_dl_002_complete_download_verified_and_finalized(server, tmp_path):
    srv = server([HfFile("a.onnx", 200_000), HfFile("b.json", 100)])
    res = run(mk(srv, tmp_path).download("op2", files_for(srv), CancelToken()))
    assert res.status == "done" and res.error_code is None
    for f in srv.files:
        snap = tmp_path / "hf" / "models--org--model" / "snapshots" / "main" / f.path
        assert hashlib.sha256(snap.read_bytes()).hexdigest() == f.sha256
        assert (tmp_path / "hf" / "models--org--model" / "blobs" / f.sha256).exists()
    assert not (tmp_path / ".downloads" / "op2").exists()
    assert res.installed and {i["path"] for i in res.installed} == {"a.onnx", "b.json"}


@pytest.mark.integration
@pytest.mark.tc("DL-024")
def test_dl_024_download_works_without_os_pwrite(server, tmp_path, monkeypatch):
    """回歸: Windows has no ``os.pwrite``, so every model download died on the first chunk. Removing it here keeps the
    fallback covered on every OS. The content is binary with newlines, so a text-mode descriptor fails the sha too."""
    monkeypatch.delattr(os, "pwrite", raising=False)
    srv = server([HfFile("a.onnx", 3 * MB)])
    res = run(mk(srv, tmp_path).download("op24", files_for(srv), CancelToken()))
    assert res.status == "done" and res.error_code is None
    f = srv.files[0]
    snap = tmp_path / "hf" / "models--org--model" / "snapshots" / "main" / f.path
    assert hashlib.sha256(snap.read_bytes()).hexdigest() == f.sha256


@pytest.mark.integration
@pytest.mark.tc("DL-003")
def test_dl_003_pause_and_resume_with_range_and_if_range(server, tmp_path):
    srv = server([HfFile("w.gguf", 4 * MB)], rate_bps=8 * MB)
    tok = CancelToken()
    d = mk(srv, tmp_path)
    seen = []

    def on_progress(ev):
        seen.append(ev["done_bytes"])
        if ev["done_bytes"] >= 0.4 * 4 * MB and not tok.requested:
            tok.pause()

    d.on_progress = on_progress
    r1 = run(d.download("op3", files_for(srv), tok))
    assert r1.status == "paused"
    meta = json.loads(next((tmp_path / ".downloads" / "op3").glob("*.part.json")).read_text(encoding="utf-8"))
    written = meta["written"]
    assert written >= 0.4 * 4 * MB
    r2 = run(mk(srv, tmp_path).download("op3", files_for(srv), CancelToken()))
    assert r2.status == "done"
    last = [r for r in srv.gets() if r["path"].startswith("/cdn/")][-1]
    assert last["range"] == f"bytes={written}-" and last["if_range"]
    assert srv.bytes_served() <= 4 * MB + 1024 * 1024  # already-downloaded bytes were not re-sent (≤ 1 chunk overlap)


@pytest.mark.chaos
@pytest.mark.tc("DL-004")
def test_dl_004_connection_drop_resumes_from_offset(server, tmp_path):
    srv = server([HfFile("w.gguf", 2 * MB)], scenario="drop-at-50%")
    clk = AutoClock()
    res = run(mk(srv, tmp_path, clock=clk).download("op4", files_for(srv), CancelToken()))
    assert res.status == "done"
    assert clk.slept and 1.6 <= clk.slept[0] <= 2.4  # 2 s backoff ±20 %
    assert srv.bytes_served() <= 2 * MB * 1.02


@pytest.mark.integration
@pytest.mark.tc("DL-005")
@pytest.mark.parametrize("keep", [False, True])
def test_dl_005_cancel_closes_socket_within_2s(server, tmp_path, keep):
    srv = server([HfFile("big.gguf", 64 * MB)], rate_bps=4 * MB)
    tok = CancelToken()
    d = mk(srv, tmp_path)
    t_cancel = {}

    def fire():
        while not [r for r in srv.gets() if r["path"].startswith("/cdn/") and r.get("sent", 0) > MB]:
            time.sleep(0.01)  # noqa: TID251 - waiting on a real server thread
        t_cancel["t"] = time.monotonic()  # noqa: TID251
        tok.cancel(keep_part=keep)

    threading.Thread(target=fire, daemon=True).start()
    res = run(d.download("op5", files_for(srv), tok))
    assert res.status == "cancelled"
    cdn = [r for r in srv.gets() if r["path"].startswith("/cdn/")]
    deadline = time.monotonic() + 3  # noqa: TID251
    while any("closed" not in r for r in cdn) and time.monotonic() < deadline:  # noqa: TID251
        time.sleep(0.01)  # noqa: TID251
    assert all(r["closed"] - t_cancel["t"] <= 2.0 for r in cdn)
    parts = list((tmp_path / ".downloads").glob("op5/*.part"))
    assert bool(parts) is keep


@pytest.mark.integration
@pytest.mark.tc("DL-006")
def test_dl_006_bad_sha_retried_once_then_permanent(server, tmp_path):
    srv = server([HfFile("w.onnx", 100_000)], scenario="bad-sha")
    res = run(mk(srv, tmp_path).download("op6", files_for(srv), CancelToken()))
    assert res.status == "failed" and res.error_code == "DL_CHECKSUM_MISMATCH" and res.permanent
    assert len([r for r in srv.gets() if r["path"].startswith("/cdn/")]) == 2
    assert not res.installed and not (tmp_path / "hf").exists()
    srv2 = server([HfFile("x.onnx", 50_000)], scenario="bad-sha-once")
    res2 = run(mk(srv2, tmp_path / "b").download("op6b", files_for(srv2), CancelToken()))
    assert res2.status == "done"


@pytest.mark.integration
@pytest.mark.tc("DL-007")
def test_dl_007_remote_changed_restarts_file(server, tmp_path):
    srv = server([HfFile("w.gguf", 4 * MB)], rate_bps=8 * MB)
    tok = CancelToken()
    d = mk(srv, tmp_path)
    d.on_progress = lambda ev: tok.pause() if ev["done_bytes"] >= MB and not tok.requested else None
    assert run(d.download("op7", files_for(srv), tok)).status == "paused"
    srv.scenario = "etag-changed"
    srv.rate_bps = None
    res = run(mk(srv, tmp_path).download("op7", files_for(srv), CancelToken()))
    assert "DL_REMOTE_CHANGED" in res.warnings
    assert res.status == "failed" or res.status == "done"  # content is the same here, so the sha still verifies
    last = [r for r in srv.gets() if r["path"].startswith("/cdn/")][-1]
    assert last["sent"] == 4 * MB  # whole file re-downloaded after the 200


@pytest.mark.integration
@pytest.mark.tc("DL-008")
def test_dl_008_429_honours_retry_after_and_reduces_segments(server, tmp_path):
    srv = server([HfFile("w.gguf", 3 * MB)], scenario="429-retry-after-5")
    clk = AutoClock()
    d = mk(srv, tmp_path, clock=clk, segment_threshold=MB, segments=4)
    res = run(d.download("op8", files_for(srv), CancelToken()))
    assert res.status == "done"
    assert 5 in clk.slept and "DL_RATE_LIMITED" in res.warnings
    assert d.segments == 2


@pytest.mark.integration
@pytest.mark.tc("DL-009")
@pytest.mark.parametrize(
    "scenario,code", [("401", "HF_AUTH_REQUIRED"), ("403-gated", "HF_GATED_ACCESS_PENDING"), ("404", "DL_NOT_FOUND")]
)
def test_dl_009_permanent_http_errors(server, tmp_path, scenario, code):
    srv = server([HfFile("w.onnx", 1000)], scenario=scenario)
    clk = AutoClock()
    res = run(mk(srv, tmp_path, clock=clk).download("op9", files_for(srv), CancelToken()))
    assert res.status == "failed" and res.error_code == code and res.permanent
    assert clk.slept == []  # permanent: no retries


@pytest.mark.integration
@pytest.mark.tc("DL-010")
def test_dl_010_disk_precheck_blocks_before_any_get(server, tmp_path):
    srv = server([HfFile("w.gguf", 10 * MB)])
    need = int(10 * MB * 1.05)
    d = mk(srv, tmp_path, disk_free=Disk(need - MB))
    with pytest.raises(DownloadError) as e:
        run(d.download("op10", files_for(srv), CancelToken()))
    assert e.value.code == "DISK_INSUFFICIENT_MODELS"
    assert e.value.details["required_bytes"] == need and e.value.details["free_bytes"] == need - MB
    assert srv.gets() == []


@pytest.mark.unit
@pytest.mark.tc("DL-012")
def test_dl_012_queue_precheck_counts_queued_downloads():
    from opagent_worker.downloads import queue_precheck

    GB = 1024**3
    b = queue_precheck([16 * GB, 15 * GB], free_bytes=24 * GB)
    assert b is not None and b["code"] == "DISK_INSUFFICIENT_MODELS"
    assert b["params"]["queued"] == 2 and b["params"]["required_bytes"] == 31 * GB
    assert "排隊的 2 個下載共需 31 GB" in b["message"]
    assert queue_precheck([10 * GB], free_bytes=24 * GB) is None


@pytest.mark.integration
@pytest.mark.tc("DL-013")
def test_dl_013_segmented_parallel_with_global_cap(server, tmp_path):
    srv = server([HfFile(f"s{i}.gguf", 3 * MB, seed=i) for i in range(3)], rate_bps=6 * MB)
    d = mk(srv, tmp_path, segment_threshold=MB, segments=4, max_connections=8)
    res = run(d.download("op13", files_for(srv), CancelToken()))
    assert res.status == "done"
    assert srv.max_active <= 8
    seg_gets = [r for r in srv.gets() if r["path"].startswith("/cdn/") and r["range"]]
    assert len(seg_gets) >= 4
    for f in srv.files:
        snap = tmp_path / "hf" / "models--org--model" / "snapshots" / "main" / f.path
        assert hashlib.sha256(snap.read_bytes()).hexdigest() == f.sha256


@pytest.mark.integration
@pytest.mark.tc("DL-013")
def test_dl_013_part_json_tracks_each_segment(server, tmp_path):
    srv = server([HfFile("w.gguf", 4 * MB)], rate_bps=4 * MB)
    tok = CancelToken()
    d = mk(srv, tmp_path, segment_threshold=MB, segments=4)
    d.on_progress = lambda ev: tok.pause() if ev["done_bytes"] > 2 * MB and not tok.requested else None
    assert run(d.download("op13b", files_for(srv), tok)).status == "paused"
    meta = json.loads(next((tmp_path / ".downloads" / "op13b").glob("*.part.json")).read_text(encoding="utf-8"))
    segs = meta["segments"]
    assert len(segs) == 4 and sum(s["written"] for s in segs) == meta["written"]
    assert all(0 <= s["written"] <= s["end"] - s["start"] + 1 for s in segs)
    assert (
        run(mk(srv, tmp_path, segment_threshold=MB, segments=4).download("op13b", files_for(srv), CancelToken())).status
        == "done"
    )


@pytest.mark.integration
@pytest.mark.tc("DL-015")
def test_dl_015_switches_to_mirror_after_3_failures(server, tmp_path):
    files = [HfFile("w.onnx", 500_000)]
    primary = server(files, scenario="5xx-x3")
    mirror = server(files)
    primary._count["5xx"] = -10  # keep failing
    clk = AutoClock()
    d = Downloader(
        Path(tmp_path), sources=[primary.url, mirror.url], reserve_floor=0, disk_free=Disk(10**12), clock=clk
    )
    res = run(d.download("op15", files_for(primary), CancelToken()))
    assert res.status == "done"
    assert any(w.startswith("DL_SOURCE_SWITCHED") or w == "DL_SOURCE_SWITCHED" for w in res.warnings)
    assert [r for r in mirror.gets() if r["path"].startswith("/cdn/")]


@pytest.mark.chaos
@pytest.mark.tc("DL-017")
def test_dl_017_finalize_crash_leaves_consistent_state(server, tmp_path):
    import subprocess
    import sys

    srv = server([HfFile("w.onnx", 300_000)])
    script = tmp_path / "run.py"
    script.write_text(
        "import asyncio, sys\nfrom pathlib import Path\n"
        "from opagent_worker.downloads import Downloader, DlFile, CancelToken\n"
        f"files=[DlFile('{REPO}','main','w.onnx',{srv.files[0].size},'{srv.files[0].sha256}')]\n"
        f"d=Downloader(Path(r'{tmp_path / 'm'}'), sources=['{srv.url}'], reserve_floor=0)\n"
        "print(asyncio.run(d.download('op17', files, CancelToken())).status)\n",
        encoding="utf-8",
    )
    env = {**__import__("os").environ, "OPAGENT_TEST_MODE": "1", "OPAGENT_CRASH_AT": "download_finalize"}
    p = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 137
    d = Downloader(tmp_path / "m", sources=[srv.url], reserve_floor=0)
    state = d.installed_state(files_for(srv))
    assert state in ("not_installed", "installed")
    res = run(d.download("op17", files_for(srv), CancelToken()))
    assert res.status == "done" and d.installed_state(files_for(srv)) == "installed"


@pytest.mark.integration
@pytest.mark.tc("DL-018")
def test_dl_018_no_symlink_permission_falls_back(server, tmp_path, monkeypatch):
    import os

    def deny(*a, **k):
        raise OSError(1314, "A required privilege is not held by the client")

    monkeypatch.setattr(os, "symlink", deny)
    srv = server([HfFile("w.onnx", 10_000)])
    res = run(mk(srv, tmp_path).download("op18", files_for(srv), CancelToken()))
    assert res.status == "done"
    snap = tmp_path / "hf" / "models--org--model" / "snapshots" / "main" / "w.onnx"
    assert snap.exists() and not snap.is_symlink()
    assert hashlib.sha256(snap.read_bytes()).hexdigest() == srv.files[0].sha256


@pytest.mark.integration
@pytest.mark.tc("DL-022")
def test_dl_022_prioritize_pauses_current(server, tmp_path):
    from opagent_worker.downloads import DownloadQueue

    srv = server([HfFile("a.gguf", 8 * MB, seed=1), HfFile("b.onnx", 200_000, seed=2)], rate_bps=4 * MB)
    q = DownloadQueue(mk(srv, tmp_path))
    fa = [f for f in files_for(srv) if f.path == "a.gguf"]
    fb = [f for f in files_for(srv) if f.path == "b.onnx"]

    async def scenario():
        q.enqueue("opA", fa)
        q.enqueue("opB", fb)
        task = asyncio.create_task(q.run())
        while q.progress.get("opA", 0) < MB:
            await asyncio.sleep(0.02)
        q.prioritize("opB")
        await task
        return q

    q = run(scenario())
    order = [e for e in q.log if e[0] in ("start", "paused", "done")]
    assert order[:4] == [("start", "opA"), ("paused", "opA"), ("start", "opB"), ("done", "opB")]
    assert ("done", "opA") in order
    resumed = [r for r in srv.gets() if r["path"].startswith("/cdn/") and r["range"] and r["range"] != "bytes=0-"]
    assert resumed  # opA resumed from its breakpoint


@pytest.mark.chaos
@pytest.mark.tc("DL-011")
def test_dl_011_disk_eaten_mid_download_pauses_then_continues(server, tmp_path):
    srv = server([HfFile("w.gguf", 8 * MB)], rate_bps=16 * MB)
    disk = Disk(10**12)
    clk = AutoClock()
    d = mk(srv, tmp_path, disk_free=disk, clock=clk, disk_check_every_bytes=MB)

    def eat(ev):
        if ev["done_bytes"] >= 2 * MB:
            disk.free = 3 * MB  # another program filled the disk: remaining 6 MB + margin no longer fits

    d.on_progress = eat
    r1 = run(d.download("op11", files_for(srv), CancelToken()))
    assert r1.status == "paused" and r1.waiting_reason == "DISK_INSUFFICIENT_MODELS"
    disk.free = 10**12  # space recovered → auto-continue
    d.on_progress = None
    r2 = run(d.download("op11", files_for(srv), CancelToken()))
    assert r2.status == "done"


@pytest.mark.integration
@pytest.mark.tc("DL-016")
def test_dl_016_mirror_only_metadata_needs_confirmation(server, tmp_path):
    files = [HfFile("w.onnx", 50_000)]
    official = server(files, scenario="404")
    official.stop()  # unreachable
    mirror = server(files)
    d = Downloader(
        Path(tmp_path), sources=[official.url, mirror.url], reserve_floor=0, disk_free=Disk(10**12), clock=AutoClock()
    )
    with pytest.raises(DownloadError) as e:
        run(d.resolve(REPO, "main", ["w.onnx"]))
    assert e.value.code == "DL_UNVERIFIED_SOURCE" and e.value.details["source"] == mirror.url
    resolved = run(d.resolve(REPO, "main", ["w.onnx"], allow_unverified=True))
    assert resolved[0].sha256 == files[0].sha256
    assert run(d.download("op16", resolved, CancelToken())).status == "done"


@pytest.mark.integration
@pytest.mark.tc("DL-021")
def test_dl_021_rate_limit(server, tmp_path):
    from opagent_core.clock import SystemClock

    srv = server([HfFile("w.gguf", 200 * MB)])
    d = mk(srv, tmp_path, clock=SystemClock(), rate_limit_bps=20 * MB)
    assert run(d.download("op21", files_for(srv), CancelToken())).status == "done"
    r = [x for x in srv.gets() if x["path"].startswith("/cdn/")][-1]
    avg = r["sent"] / (r["closed"] - r["first_byte"])  # measured by the server over the transfer itself
    assert 18 * MB <= avg <= 22 * MB
