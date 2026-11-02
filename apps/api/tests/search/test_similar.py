"""find_similar (05 §2.2; 09b SRCH-IMG-001): k-NN on the stored vector, no inference; self and the same duplicate
group are excluded by default."""

import math

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from sqlalchemy import text


def _vec(deg):
    r = math.radians(deg)
    return [math.cos(r), math.sin(r), 0.0]


@pytest.mark.integration
@pytest.mark.tc("SRCH-IMG-001")
def test_img_001_find_similar(embedded_pg):
    c = TestClient(create_app(dsn=embedded_pg.dsn))
    eng = engine_for(embedded_pg.dsn)
    angles = {"q": 0, "a": 5, "b": 20, "dup": 1, "c": 40, "far": 90}
    with eng.begin() as cx:
        sid = cx.execute(
            text("insert into embedding_space(model_key, fingerprint, dim) values ('m','f',3) returning id")
        ).scalar_one()
        for aid, deg in angles.items():
            cx.execute(
                text("insert into asset(id, workspace_id, content_hash, size_bytes) values (:a,'default',:h,1)"),
                {"a": aid, "h": "h" + aid},
            )
            cx.execute(
                text("insert into asset_embedding(asset_id, space_id, vec) values (:a, :s, :v)"),
                {"a": aid, "s": sid, "v": str(_vec(deg))},
            )
        cx.execute(text("insert into duplicate_member(group_id, asset_id) values ('g1','q'), ('g1','dup')"))
    r = c.get("/api/v1/assets/q/similar", params={"k": 20, "min_score": 0.75})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["asset_id"] for i in items] == ["a", "b", "c"]  # cos(40°) = 0.766 ≥ 0.75; 90° excluded
    assert all(items[i]["score"] >= items[i + 1]["score"] for i in range(len(items) - 1))
    with_dups = c.get("/api/v1/assets/q/similar", params={"k": 20, "min_score": 0.75, "exclude_duplicates": False})
    assert [i["asset_id"] for i in with_dups.json()["items"]][0] == "dup"
    assert c.get("/api/v1/assets/nope/similar").status_code == 404
    eng.dispose()
