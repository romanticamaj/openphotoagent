import os
import subprocess
import sys

import pytest

from .test_models_api import EMB, _drain


@pytest.mark.chaos
@pytest.mark.tc("DL-014")
def test_dl_014_kill9_then_auto_resume(env, tmp_path):
    c = env["client"]
    srv = env["srv"]
    srv.rate_bps = 400_000  # slow enough to die mid-file
    op = c.post("/api/v1/workers/w1/installs", json={"variant": f"{EMB}@onnx-int8"}).json()["operation_id"]
    # a separate worker process starts the download and is killed -9 after its 2nd chunk report
    code = (
        "import sys\nfrom fastapi.testclient import TestClient\nfrom opagent_api.app import create_app\n"
        "from opagent_worker.agent import WorkerAgent\n"
        f"c = TestClient(create_app(dsn={env['dsn']!r}, registry_dir={str(env['tmp'] / 'registry')!r}))\n"
        f"w = WorkerAgent(c, roots=[], home={str(env['tmp'] / 'w')!r}, worker_id='w1', hw_profile='rtx5070ti-win11', "
        f"models_root={str(env['tmp'] / 'models')!r}, download_sources=[{srv.url!r}])\n"
        "w.run_once()\n"
    )
    e = {**os.environ, "OPAGENT_TEST_MODE": "1", "OPAGENT_CRASH_AT": "download_chunk", "OPAGENT_CRASH_NTH": "2"}
    p = subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True, timeout=120)
    assert p.returncode == 137, p.stderr[-2000:]
    srv.rate_bps = None
    w = env["worker"]
    w.register()  # restart: the control plane marks the orphaned download interrupted and re-queues it
    o = c.get(f"/api/v1/operations/{op}").json()
    assert o["status"] in ("interrupted", "queued") and o.get("auto_resumed") is True
    _drain(w)
    o = c.get(f"/api/v1/operations/{op}").json()
    assert o["status"] == "succeeded"
    ranged = [r for r in srv.gets() if r["path"].startswith("/cdn/") and r["range"] and not r["range"].startswith("bytes=0-")]
    assert ranged  # continued from the .part, not from zero
