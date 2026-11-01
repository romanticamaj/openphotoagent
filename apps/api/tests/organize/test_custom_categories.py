"""Custom categories with a description and example photos (03 §3.3, 04 §3.2 ④; backs E2E-B-003)."""

import pytest
from opagent_inference.fake import FakeBackend

from .test_by_content import _drain, _photos, _setup, _src


def _preview(client, w, src, categories):
    pv = client.post(
        "/api/v1/previews", json={"source_id": src, "preset": "custom", "categories": categories, "k": 10}
    ).json()
    _drain(w)
    return client.get(f"/api/v1/previews/{pv['id']}").json(), pv["id"]


@pytest.mark.integration
@pytest.mark.tc("INF-CAS-010")
def test_custom_category_with_examples_estimates_library_hits(embedded_pg, sandbox_tree, tmp_path):
    _photos(sandbox_tree.photos, "", 20)
    client, w = _setup(
        embedded_pg.dsn,
        sandbox_tree.roots,
        tmp_path / "w",
        FakeBackend(scores="mixed", vectors="semantic"),
        FakeBackend(vlm_p=0.9, vectors="semantic"),
    )
    src = _src(client)
    first, _ = _preview(client, w, src, [{"key": "cats", "name": "貓咪", "description": "家裡的貓"}])
    assert first["status"] == "ready" and len(first["items"]) == 10
    examples = [it["asset_id"] for it in first["items"][:3]]
    pv, pid = _preview(
        client, w, src, [{"key": "cats", "name": "貓咪", "description": "家裡的貓", "examples": examples}]
    )
    from opagent_db.schema import engine_for, job
    from sqlalchemy import select

    with engine_for(embedded_pg.dsn).connect() as c:
        tc = c.execute(select(job.c.params).where(job.c.id == pid)).scalar()["task_config"]
    cats = next(x for x in tc["classes"] if x["id"] == "cats")
    assert len(cats["examples"]) == 3 and all({"root_alias", "rel"} <= set(e) for e in cats["examples"])
    assert {x["id"] for x in tc["classes"]} == {"cats", "other"}
    est = pv["estimate"]
    assert est is not None and set(est["distribution"]) <= {"cats", "other"}
    assert est["distribution"].get("cats", 0) >= 3  # the example photos themselves land in their category
    assert {it["asset_id"] for it in pv["items"] if it["category_key"] == "cats"} >= set(examples)
