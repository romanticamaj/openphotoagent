"""PERF-004: scan throughput on 10,000 photos (09a). Nightly: records files/s per machine and fails on a > 15 %
regression against that machine's recorded baseline (``fixtures/perf/scan-baselines.json``)."""

import json
import os
import platform
import shutil
import time
from datetime import timedelta, timezone
from pathlib import Path

import pytest
from opagent_fs.safepath import RootConfig, Sandbox
from opagent_worker.scanner import scan

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"
BASELINES = Path(__file__).resolve().parents[4] / "fixtures/perf/scan-baselines.json"
N = 10_000


@pytest.mark.nightly
@pytest.mark.perf
@pytest.mark.tc("PERF-004")
def test_perf_004_scan_throughput_does_not_regress(sandbox_tree, tmp_path):
    srcs = sorted(FX.glob("IMG_*.jpg"))
    for i in range(N):
        d = sandbox_tree.photos / f"{i // 500:02d}"
        d.mkdir(exist_ok=True)
        dst = d / f"{i:05d}.jpg"
        shutil.copyfile(srcs[i % len(srcs)], dst)
        with dst.open("ab") as fh:
            fh.write(i.to_bytes(4, "big"))  # distinct content hashes
    sb = Sandbox([RootConfig(**r) for r in sandbox_tree.roots])
    t0 = time.perf_counter()
    res = scan(sb, "photos", "", ["**/*"], [], tz=timezone(timedelta(hours=8)), thumbs_dir=tmp_path / "thumbs")
    secs = time.perf_counter() - t0
    assert len(res.entries) == N
    rate = N / secs
    machine = os.environ.get("OPAGENT_PERF_MACHINE") or f"{platform.system()}-{platform.machine()}-{os.cpu_count()}cpu"
    base = json.loads(BASELINES.read_text()) if BASELINES.exists() else {}
    print(f"PERF-004 {machine}: {rate:.0f} files/s ({secs:.1f} s for {N})")
    ref = base.get(machine)
    if ref is None:  # first run on this machine records its baseline (commit it from the nightly artefact)
        base[machine] = {"files_per_s": round(rate, 1)}
        BASELINES.write_text(json.dumps(base, indent=1, sort_keys=True) + "\n")
        return
    assert rate >= ref["files_per_s"] * 0.85, f"scan throughput regressed: {rate:.0f} < 85% of {ref['files_per_s']}"
