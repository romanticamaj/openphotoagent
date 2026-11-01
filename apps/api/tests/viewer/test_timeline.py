"""Viewer backend: timeline buckets, derived media variants, info panel (10 §2, §3.4; VIEW-009/011)."""

import shutil
import statistics
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from opagent_inference.fake import FakeBackend
from opagent_worker.agent import WorkerAgent
from sqlalchemy import text

FX = Path(__file__).resolve().parents[4] / "fixtures/photos"


def _drain(w):
    for _ in range(400):
        a, b = w.run_once(), w.process_items_once()
        if not a and not b:
            return


def _library(dsn, roots, tmp):
    """Add the photos folder as a library source and scan it without organizing anything."""
    client = TestClient(create_app(dsn=dsn, thumbs_dir=tmp / "w" / "thumbs"))
    w = WorkerAgent(
        client,
        roots=roots,
        home=tmp / "w",
        backend=FakeBackend(vlm_p=0.9),
        embedder=FakeBackend(scores="mixed"),
        worker_id="w1",
    )
    w.register()
    src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
    r = client.post(f"/api/v1/sources/{src}/scan")
    assert r.status_code == 202, r.text
    _drain(w)
    return client, w, src


def _all_assets(client):
    out = []
    for b in client.get("/api/v1/timeline/buckets").json()["buckets"]:
        out += client.get(f"/api/v1/timeline/buckets/{b['month']}").json()["assets"]
    return out


@pytest.mark.integration
@pytest.mark.tc("VIEW-011")
def test_view_011_info_panel_fields(embedded_pg, sandbox_tree, tmp_path):
    shutil.copyfile(FX / "basic/IMG_0001.jpg", sandbox_tree.photos / "a.jpg")
    shutil.copyfile(FX / "no-exif/NOEXIF_0001.jpg", sandbox_tree.photos / "b.jpg")
    client, w, src = _library(embedded_pg.dsn, sandbox_tree.roots, tmp_path)
    infos = {a["id"]: client.get(f"/api/v1/assets/{a['id']}").json() for a in _all_assets(client)}
    assert len(infos) == 2
    cam = next(i for i in infos.values() if i["exif"].get("camera_make"))
    assert cam["exif"]["camera_make"] == "OPA" and cam["exif"]["camera_model"] == "Synth-1"
    assert cam["width"] and cam["height"] and cam["taken_at"].startswith("2023-01-15")
    bare = next(i for i in infos.values() if not i["exif"].get("camera_make"))
    assert bare["date_source"] == "mtime"
    for i in infos.values():  # neither photo has GPS: no place, no coordinates, nothing invented
        assert "place" not in i and "gps" not in i["exif"]
    # AI fields: every label names the model fingerprint and the decision layer
    client.post(
        "/api/v1/jobs",
        json={
            "kind": "organize",
            "preset": "by-content-12",
            "source_id": src,
            "target": {"root_alias": "sorted", "sub_path": ""},
        },
    )
    _drain(w)
    for aid in infos:
        labs = client.get(f"/api/v1/assets/{aid}").json()["labels"]
        assert labs and all(lab["model_fingerprint"] and lab["source"] for lab in labs)


@pytest.mark.integration
def test_media_variants_are_served_from_the_derived_cache(embedded_pg, sandbox_tree, tmp_path):
    """Backend half of VIEW-008: ThumbHash in the bucket payload, t256/t512/p1024 from the thumbnail cache."""
    shutil.copyfile(FX / "basic/IMG_0001.jpg", sandbox_tree.photos / "a.jpg")
    client, _, _ = _library(embedded_pg.dsn, sandbox_tree.roots, tmp_path)
    [a] = _all_assets(client)
    assert a["thumbhash"] and a["w"] and a["h"]
    for v in ("t256", "t512", "p1024"):
        r = client.get(f"/api/v1/assets/{a['id']}/media", params={"variant": v})
        assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
        assert "immutable" in r.headers["cache-control"]
    assert client.get(f"/api/v1/assets/{a['id']}/media", params={"variant": "../../etc/passwd"}).status_code == 422
    assert client.get("/api/v1/assets/nope/media", params={"variant": "t256"}).status_code == 404


@pytest.mark.integration
@pytest.mark.perf
@pytest.mark.tc("VIEW-009")
def test_view_009_timeline_latency(embedded_pg):
    client = TestClient(create_app(dsn=embedded_pg.dsn))
    eng = engine_for(embedded_pg.dsn)
    with eng.begin() as c:
        c.execute(
            text(
                "insert into asset(id, workspace_id, content_hash, size_bytes, taken_at, local_at, width, height, "
                "thumbhash) select 'a'||g, 'default', 'h'||g, 1, timestamptz '2016-01-01Z' + (g * interval '50 min'), "
                "timestamp '2016-01-01' + (g * interval '50 min'), 4000, 3000, 'AAAA' "
                "from generate_series(1, 100000) g"
            )
        )
        c.execute(text("analyze asset"))
    eng.dispose()

    def p95(f):
        f()  # warm-up
        ds = []
        for _ in range(30):
            t0 = time.perf_counter()  # noqa: TID251 - latency budget
            f()
            ds.append(time.perf_counter() - t0)  # noqa: TID251
        return statistics.quantiles(ds, n=20)[18]

    buckets = client.get("/api/v1/timeline/buckets").json()["buckets"]
    assert sum(b["count"] for b in buckets) == 100_000
    assert p95(lambda: client.get("/api/v1/timeline/buckets")) < 0.050
    big = max(buckets, key=lambda b: b["count"])
    assert big["count"] <= 5000
    assert p95(lambda: client.get(f"/api/v1/timeline/buckets/{big['month']}")) < 0.080
