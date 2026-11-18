"""v0.5 rows of the preflight catalogue (09a PF-058, PF-059; 06 §4.7 #58/#59): the same checks as every other
row — the code, fix types in order with a primary first, a consequence on every alternative, both languages."""

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
GOLDEN = {
    r["id"]: r
    for r in yaml.safe_load((ROOT / "fixtures/golden/preflight_catalog.yaml").read_text(encoding="utf-8"))["rows"]
}
I18N = {
    lang: json.loads((ROOT / f"packages/i18n/errors.{lang}.json").read_text(encoding="utf-8"))
    for lang in ("zh-TW", "en")
}


def check(pf, pid):
    row = GOLDEN[pid]
    code = row["codes"][0]
    hit = [b for b in pf["blockers"] if b["code"] == code]
    assert hit, pf
    p = hit[0]
    assert [f["type"] for f in p["fixes"]] == [f["type"] for f in row["fixes"]]
    assert p["fixes"][0]["primary"] is True and all(not f["primary"] for f in p["fixes"][1:])
    assert all(f.get("consequence") for f in p["fixes"] if f["type"] == "choose_alternative")
    for lang in ("zh-TW", "en"):
        assert {"title", "message", "cta"} <= set(I18N[lang][code]), (lang, code)
    assert pf["ready"] is False
    return p


@pytest.mark.integration
@pytest.mark.tc("PF-058")
def test_pf_058_egress_policy_blocks_cloud_endpoint(cp_app, as_role):
    admin = as_role("ws_admin")
    from sqlalchemy import text

    with cp_app.state.opa.engine.begin() as c:
        c.execute(text("insert into worker(id, name, roots) values ('w1', 'gpu', '[]')"))
        c.execute(
            text(
                "insert into source(id, workspace_id, worker_id, root_alias, include, exclude)"
                " values ('s-x', 'default', 'w1', 'photos', '[]', '[]')"
            )
        )
    job = {"kind": "organize", "source_id": "s-x", "target": {"root_alias": "sorted"}}
    body = {"action": "job.start", "params": {**job, "inference": {"endpoint": "https://api.example.com/v1"}}}
    assert not [
        b for b in admin.post("/api/v1/preflight", json=body).json()["blockers"] if b["code"] == "EGRESS_POLICY_BLOCKS"
    ]
    for mode in ("results-only", "edge-thumbs"):
        assert admin.put("/api/v1/workspace/egress-policy", json={"mode": mode}).status_code == 200
        check(admin.post("/api/v1/preflight", json=body).json(), "PF-058")
    local = {"action": "job.start", "params": {**job, "inference": {"endpoint": "http://127.0.0.1:8080/v1"}}}
    pf = admin.post("/api/v1/preflight", json=local).json()  # a model server on the worker's own machine is local
    assert not [b for b in pf["blockers"] if b["code"] == "EGRESS_POLICY_BLOCKS"]
    r = admin.post("/api/v1/jobs", json=body["params"])  # the endpoint enforces the same engine (PF-073)
    assert r.status_code == 409 and "EGRESS_POLICY_BLOCKS" in [b["code"] for b in r.json()["blockers"]]


@pytest.mark.integration
@pytest.mark.tc("PF-059")
def test_pf_059_permission_denied_role(cp_app, as_role):
    reviewer = as_role("ws_reviewer")
    body = {"action": "output.apply", "params": {"plan_id": "p1"}}
    p = check(reviewer.post("/api/v1/preflight", json=body).json(), "PF-059")
    assert p["params"]["permission"] == "output:apply" and p["requires_permission"] == "output:apply"
    admin = as_role("ws_admin")
    assert not [
        b
        for b in admin.post("/api/v1/preflight", json=body).json()["blockers"]
        if b["code"] == "PERMISSION_DENIED_ROLE"
    ]
    undo = reviewer.post("/api/v1/preflight", json={"action": "output.undo", "params": {"plan_id": "p1"}}).json()
    assert "PERMISSION_DENIED_ROLE" in [b["code"] for b in undo["blockers"]]
