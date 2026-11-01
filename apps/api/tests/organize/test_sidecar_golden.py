"""ORG-006: JSON sidecars per photo and a CSV per batch match fixtures/golden/sidecar/* (timestamps masked).

Goldens are (re)written only with OPAGENT_UPDATE_GOLDEN=1 and then reviewed by a person before committing.
"""

import os
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_inference.fake import FakeBackend
from opagent_worker.agent import WorkerAgent

ROOT = Path(__file__).resolve().parents[4]
FX = ROOT / "fixtures/photos/basic"
GOLDEN = ROOT / "fixtures/golden/sidecar"


def _mask(text: str) -> str:
    text = re.sub(r'"generated_at": "[^"]+"', '"generated_at": "<masked>"', text)
    return re.sub(r",[0-9T:+\-.]+Z?\r?$", ",<masked>", text, flags=re.M)


@pytest.mark.integration
@pytest.mark.tc("ORG-006")
def test_org_006_sidecars_match_golden(embedded_pg, sandbox_tree, tmp_path):
    for i in range(1, 6):
        shutil.copyfile(FX / f"IMG_000{i}.jpg", sandbox_tree.photos / f"IMG_000{i}.jpg")
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    w = WorkerAgent(
        client,
        roots=sandbox_tree.roots,
        home=tmp_path / "w",
        backend=FakeBackend(vlm_p=0.95),
        embedder=FakeBackend(scores="high"),
        worker_id="w1",
    )
    w.register()
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
    j = client.post(
        "/api/v1/jobs",
        json={
            "source_id": src,
            "preset": "by-content-12",
            "target": {"root_alias": "sorted", "sub_path": ""},
            "advanced": {"sidecars": ["json", "csv"]},
        },
    ).json()
    for _ in range(200):
        if not (w.run_once() or w.process_items_once()):
            break
    job = client.get(f"/api/v1/jobs/{j['id']}").json()
    plan = client.get(f"/api/v1/output-plans/{job['plan_id']}").json()
    kinds = sorted(a["action"] for a in plan["actions"])
    assert kinds.count("copy") == 5 and kinds.count("sidecar_json") == 5 and kinds.count("sidecar_csv") == 1
    client.post(f"/api/v1/output-plans/{plan['id']}/approve", json={})
    while w.run_once():
        pass
    produced = {
        p.relative_to(sandbox_tree.sorted).as_posix(): _mask(p.read_text(encoding="utf-8"))
        for p in sorted(sandbox_tree.sorted.rglob("*"))
        if p.is_file() and p.suffix in (".json", ".csv")
    }
    assert len(produced) == 6
    if os.environ.get("OPAGENT_UPDATE_GOLDEN") == "1":
        shutil.rmtree(GOLDEN, ignore_errors=True)
        for rel, text in produced.items():
            (GOLDEN / rel).parent.mkdir(parents=True, exist_ok=True)
            (GOLDEN / rel).write_text(text, encoding="utf-8")
    expected = {
        p.relative_to(GOLDEN).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(GOLDEN.rglob("*"))
        if p.is_file()
    }
    assert produced == expected
    # undo removes the sidecars like any other output (invariant 3)
    client.post(f"/api/v1/output-plans/{plan['id']}/undo")
    while w.run_once():
        pass
    assert not [p for p in sandbox_tree.sorted.rglob("*") if p.is_file() and ".opagent-trash" not in p.parts]
