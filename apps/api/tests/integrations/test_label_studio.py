"""Label Studio (02 §8.3; 09b §7.4 LS-001..003): export tasks with predictions, a safe labeling config, and
annotations coming back as human labels — never a re-run of inference."""

import json
import xml.etree.ElementTree as ET

import pytest
from opagent_api.labelstudio import labeling_config
from opagent_testing.mcp_harness import M


@pytest.fixture
def m(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path)
    x.ids = [x.add(f"2025/trip/p{i}.jpg", caption="貓", tags=("animal.cat",), category="pets") for i in range(10)]
    x.index()
    yield x
    x.eng.dispose()


@pytest.mark.contract
@pytest.mark.tc("LS-001")
def test_ls_001_export_tasks(m, tmp_path):
    r = m.client.post("/api/v1/export/label-studio", json={"asset_ids": m.ids, "preset": "by-content-12"}, headers=m.ui)
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["tasks"]) == 10
    t = out["tasks"][0]
    assert t["data"]["image"] == "/data/local-files/?d=photos/2025/trip/p0.jpg"
    assert t["data"]["opagent_id"] == m.ids[0]
    pred = t["predictions"][0]
    assert pred["model_version"] == "f" and 0 < pred["score"] <= 1
    assert pred["result"][0]["value"] == {"choices": ["pets"]} and pred["result"][0]["from_name"] == "category"
    blob = json.dumps(out)
    assert str(tmp_path) not in blob and "/tmp/" not in blob  # no host paths
    ET.fromstring(out["config"])  # the config parses


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("LS-002")
def test_ls_002_labeling_config_escapes_names():
    cats = [{"key": "a_b", "zh-TW": 'A<b>&"c"'}, {"key": "other", "zh-TW": "其他'"}]
    xml = labeling_config(cats)
    root = ET.fromstring(xml)
    choices = root.find("Choices")
    assert [c.get("value") for c in choices.findall("Choice")] == ["a_b", "other"]
    assert [c.get("alias") for c in choices.findall("Choice")] == ['A<b>&"c"', "其他'"]
    assert root.find("Image").get("value") == "$image"
    assert "<b>" not in xml  # escaped, not markup


@pytest.mark.integration
@pytest.mark.tc("LS-003")
def test_ls_003_import_annotations_as_human_labels(m):
    export = [
        {
            "data": {"opagent_id": m.ids[0]},
            "annotations": [{"result": [{"from_name": "category", "value": {"choices": ["food"]}}]}],
        },
        {
            "data": {"opagent_id": m.ids[1]},
            "annotations": [{"result": [{"from_name": "category", "value": {"choices": ["not-a-category"]}}]}],
        },
        {
            "data": {"opagent_id": "gone"},
            "annotations": [{"result": [{"from_name": "category", "value": {"choices": ["pets"]}}]}],
        },
        {"data": {"opagent_id": m.ids[2]}, "annotations": []},
    ]
    before = m.sql("select count(*) from worker_task").scalar(), m.sql("select count(*) from job").scalar()
    r = m.client.post("/api/v1/import/label-studio", json={"export": export, "preset": "by-content-12"}, headers=m.ui)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep == {
        "applied": 1,
        "unknown_labels": [{"asset_id": m.ids[1], "label": "not-a-category"}],
        "missing_assets": ["gone"],
        "unannotated": 1,
    }
    row = m.sql("select status, corrected from review where asset_id = :a and status = 'corrected'", a=m.ids[0]).one()
    assert row.corrected["category"] == "food"
    assert (m.sql("select count(*) from worker_task").scalar(), m.sql("select count(*) from job").scalar()) == before
    assert (
        m.client.post("/api/v1/import/label-studio", json={"export": [], "preset": "by-content-12"}).status_code == 403
    )
