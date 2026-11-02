"""Duplicate groups (05 §2.2; 09b SRCH-DUP-001/002): exact = one asset at several locations, near = pHash or
embedding neighbours after `recluster`. Rows only — the API never reads photo files (invariant 1)."""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from opagent_worker.imaging import phash
from PIL import Image, ImageFilter
from sqlalchemy import text

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"


def _env(dsn):
    c = TestClient(create_app(dsn=dsn))
    c.post("/worker-api/v1/register", json={"worker_id": "w1", "roots": [{"alias": "photos", "mode": "rw"}]})
    src = c.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
    return c, src, engine_for(dsn)


def _asset(eng, src, aid, size, ph, locations, w=4000, h=3000):
    with eng.begin() as cx:
        cx.execute(
            text(
                "insert into asset(id, workspace_id, content_hash, size_bytes, width, height, phash)"
                " values (:a, 'default', :h, :s, :w, :ht, :p)"
            ),
            {"a": aid, "h": "h-" + aid, "s": size, "w": w, "ht": h, "p": ph},
        )
        for i, rel in enumerate(locations):
            cx.execute(
                text("insert into asset_location(id, asset_id, source_id, rel_path, size) values (:i, :a, :s, :r, :z)"),
                {"i": f"{aid}-{i}", "a": aid, "s": src, "r": rel, "z": size},
            )


@pytest.mark.integration
@pytest.mark.tc("SRCH-DUP-001")
def test_dup_001_same_content_at_three_locations(embedded_pg):
    c, src, eng = _env(embedded_pg.dsn)
    _asset(eng, src, "x", 1_000, "0f0f0f0f0f0f0f0f", ["a/x.jpg", "b/x.jpg", "c/x copy.jpg"])
    _asset(eng, src, "y", 5_000, "ffff0000ffff0000", ["a/y.jpg"])
    groups = c.get("/api/v1/duplicates").json()["groups"]
    g = [g for g in groups if g["reason"] == "hash"]
    assert len(g) == 1 and g[0]["reclaimable_bytes"] == 2 * 1_000
    assert sorted(m["rel_path"] for m in g[0]["members"]) == ["a/x.jpg", "b/x.jpg", "c/x copy.jpg"]
    assert g[0]["representative"]["asset_id"] == "x"
    eng.dispose()


def _variant(img, kind):
    if kind == "scaled":
        out = img.resize((img.width // 2, img.height // 2))
    elif kind == "recompressed":
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=35)
        out = Image.open(io.BytesIO(buf.getvalue()))
    elif kind == "cropped":
        dx, dy = img.width // 25, img.height // 25
        out = img.crop((dx, dy, img.width - dx, img.height - dy))
    else:
        out = img.filter(ImageFilter.GaussianBlur(0.6))
    return phash(out)


@pytest.mark.integration
@pytest.mark.tc("SRCH-DUP-002")
def test_dup_002_near_duplicates_group_and_different_photos_do_not(embedded_pg):
    c, src, eng = _env(embedded_pg.dsn)
    base = Image.open(FX / "IMG_0001.jpg").convert("RGB")
    other = Image.open(FX / "IMG_0007.jpg").convert("RGB")
    _asset(eng, src, "orig", 9_000, phash(base), ["orig.jpg"], 4000, 3000)
    for i, kind in enumerate(["scaled", "recompressed", "cropped", "blurred"]):
        _asset(eng, src, kind, 3_000 + i, _variant(base, kind), [f"{kind}.jpg"], 2000, 1500)
    _asset(eng, src, "other", 8_000, phash(other), ["other.jpg"])
    r = c.post("/api/v1/duplicates/recluster")
    assert r.status_code in (200, 202), r.text
    near = [g for g in c.get("/api/v1/duplicates").json()["groups"] if g["reason"] == "near"]
    assert len(near) == 1
    assert sorted(m["asset_id"] for m in near[0]["members"]) == ["blurred", "cropped", "orig", "recompressed", "scaled"]
    assert near[0]["representative"]["asset_id"] == "orig"  # highest resolution (SRCH-DUP-003)
    assert near[0]["reclaimable_bytes"] == sum(3_000 + i for i in range(4))
    eng.dispose()
