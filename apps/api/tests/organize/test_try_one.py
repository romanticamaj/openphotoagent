import json

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_inference.fake import FakeBackend
from opagent_worker.agent import WorkerAgent

from .test_by_content import _drain, _photos, _src


@pytest.mark.integration
@pytest.mark.tc("ORG-008")
def test_org_008_try_one_streams_stages_on_interactive_lane(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "t", 6)
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    w = WorkerAgent(client, roots=sandbox_tree.roots, home=tmp_path / "w", backend=FakeBackend(vlm_p=0.9), embedder=FakeBackend(scores="low"), worker_id="w1")
    w.register()
    src = _src(client)
    pv = client.post("/api/v1/previews", json={"source_id": src, "k": 3}).json()
    _drain(w)
    aid = client.get(f"/api/v1/previews/{pv['id']}").json()["items"][0]["asset_id"]
    # a normal-lane job is queued first; the interactive "try" must still run before its scan
    client.post("/api/v1/jobs", json={"kind": "organize", "preset": "by-content-12", "source_id": src, "target": {"root_alias": "sorted"}})
    t = client.post(f"/api/v1/previews/{pv['id']}/try", json={"asset_id": aid}).json()
    assert t["lane"] == "interactive"
    assert w.run_once() is True
    got = client.get(f"/api/v1/previews/{pv['id']}/try/{t['id']}").json()
    assert got["status"] == "done" and got["result"]["decision"] in ("accepted", "needs_review", "other")
    with client.stream("GET", "/api/v1/events", params={"once": 1}) as s:
        body = "".join(s.iter_text())
    stages = [
        json.loads(chunk.split("data: ", 1)[1])["stage"]
        for chunk in body.split("\n\n")
        if "event: preview.stage" in chunk and t["id"] in chunk
    ]
    assert stages == ["rules", "embedding", "vlm", "decision"]
