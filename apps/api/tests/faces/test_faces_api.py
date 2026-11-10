"""Faces end to end over the API (09b §4): opt-in, ingestion and online assignment, clustering, person management,
find this person, purge and privacy. Embeddings come from FakeFaceBackend, posted the way the Worker posts them."""

import re
from datetime import UTC, datetime

import pytest
import sqlalchemy.exc
from opagent_inference.fake_faces import FaceSpec, FakeFaceBackend
from opagent_testing.clock import FakeClock
from opagent_testing.mcp_harness import M

CONSENT = {"consent": True, "consent_text_version": "faces-v1"}


class Faces(M):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fb = FakeFaceBackend()

    def enable(self, **kw):
        r = self.client.post("/api/v1/faces/enable", json={**CONSENT, **kw}, headers=self.ui)
        assert r.status_code == 200, r.text
        return r.json()

    def photo(self, rel, persons=None, plan=None, **kw):
        aid = self.add(rel, **kw)
        faces = (
            self.fb.detect(rel) if plan is None else FakeFaceBackend(plan={rel.rsplit("/", 1)[-1]: plan}).detect(rel)
        )
        r = self.client.post(
            "/worker-api/v1/faces",
            json={
                "worker_id": "w1",
                "asset_id": aid,
                "fingerprint": self.fb.fingerprint,
                "truncated": False,
                "faces": [{k: v for k, v in f.items() if k != "person_hint"} for f in faces],
            },
            headers=self.ui,
        )
        assert r.status_code == 200, r.text
        return aid, r.json()["face_ids"]

    def get(self, path):
        r = self.client.get(f"/api/v1{path}", headers=self.ui)
        assert r.status_code == 200, r.text
        return r.json()

    def post(self, path, body=None, status=200):
        r = self.client.post(f"/api/v1{path}", json=body or {}, headers=self.ui)
        assert r.status_code == status, r.text
        return r.json()


@pytest.fixture
def f(embedded_pg, tmp_path):
    x = Faces(embedded_pg.dsn, tmp_path, clock=FakeClock(datetime(2026, 11, 2, tzinfo=UTC)))
    yield x
    x.eng.dispose()


def _library(f, people=(1, 2, 3), per=8):
    ids = {}
    for p in people:
        for i in range(per):
            ids.setdefault(p, []).append(f.photo(f"2025/{p}/IMG_{i}_p{p}.jpg")[1][0])
    return ids


@pytest.mark.integration
@pytest.mark.tc("FACE-GATE-001")
def test_gate_001_off_by_default(f):
    s = f.get("/faces/settings")
    assert s["enabled"] is False and s["show_people"] is False
    r = f.client.post(
        "/worker-api/v1/faces",
        json={"worker_id": "w1", "asset_id": f.add("a.jpg"), "fingerprint": "x", "truncated": False, "faces": []},
        headers=f.ui,
    )
    assert r.status_code == 409 and r.json()["code"] == "FACES_DISABLED"
    assert f.sql("select count(*) from face").scalar() == 0
    assert (
        f.sql("select count(*) from pg_tables where schemaname='bio' and tablename like 'face_embedding_w%'").scalar()
        == 0
    )


@pytest.mark.integration
@pytest.mark.tc("FACE-GATE-002", "FACE-GATE-003")
def test_gate_002_003_consent_recorded_and_limited_to_sources(f):
    r = f.client.post("/api/v1/faces/enable", json={"consent": False, "consent_text_version": "faces-v1"}, headers=f.ui)
    assert r.status_code == 422 and r.json()["code"] == "FACE_CONSENT_REQUIRED"
    f.enable(sources=[f.src])
    c = f.sql("select action, consent_text_version, source_ids from face_consent").one()
    assert c.action == "enable" and c.consent_text_version == "faces-v1" and c.source_ids == [f.src]
    assert f.get("/faces/settings")["show_people"] is True
    f.photo("a/IMG_p1.jpg")
    other = f.add("b/IMG_p2.jpg", source=f.inbox)
    r = f.client.post(
        "/worker-api/v1/faces",
        json={"worker_id": "w1", "asset_id": other, "fingerprint": "x", "truncated": False, "faces": []},
        headers=f.ui,
    )
    assert r.status_code == 409 and r.json()["code"] == "FACES_NOT_FOR_SOURCE"
    assert f.client.get("/worker-api/v1/faces/plan", params={"asset_id": other}, headers=f.ui).json() == {"run": False}


@pytest.mark.integration
@pytest.mark.tc("FACE-ASN-001", "FACE-CLU-001")
def test_cold_start_clusters_then_online_assignment(f):
    f.enable()
    _library(f, people=range(1, 7), per=12)
    run = f.post("/faces/cluster")
    assert run["clusters"] == 6
    groups = f.get("/people")["people"]
    assert len(groups) == 6 and all(g["status"] == "unnamed" and g["face_count"] == 12 for g in groups)
    truth = {}
    for g in groups:
        rels = {
            r
            for (r,) in f.sql(
                "select l.rel_path from face x join asset_location l on l.asset_id=x.asset_id where x.person_id=:p",
                p=g["id"],
            )
        }
        persons = {re.search(r"_p(\d+)", r).group(1) for r in rels}
        assert len(persons) == 1  # pure groups: ARI 1.0 on the double
        truth[persons.pop()] = g["id"]
    f.post(f"/people/{truth['1']}/name", {"name": "小明"})
    _, (fid,) = f.photo("2025/new/IMG_99_p1.jpg")
    row = f.sql("select person_id, state from face where id=:i", i=fid).one()
    assert (row.person_id, row.state) == (truth["1"], "auto")
    a = f.sql("select kind, method, actor from face_assignment where face_id=:i", i=fid).one()
    assert (a.kind, a.method, a.actor) == ("auto", "online_knn", "system")


@pytest.mark.integration
@pytest.mark.tc("FACE-CLU-002", "FACE-CLU-004")
def test_clu_002_human_decisions_frozen(f):
    f.enable()
    ids = _library(f, people=(1, 2), per=10)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    f.post(f"/people/{p1}/name", {"name": "小明"})
    for fid in ids[1][:5]:
        f.post(f"/faces/{fid}/confirm", {"person_id": p1})
    for fid in ids[1][5:7]:
        f.post(f"/faces/{fid}/reject", {"person_id": p1})
    before = f.sql(
        "select id, face_id, person_id, kind, superseded_at from face_assignment where person_id=:p "
        "and kind in ('confirmed','rejected') order by id",
        p=p1,
    ).all()
    named = f.sql("select display_name, status, is_frozen from person where id=:p", p=p1).one()
    for i in range(2):
        f.photo(f"2025/x/IMG_{i}_p9.jpg")  # two strangers: a group under 3 faces folds
    f.post("/faces/cluster")
    assert (
        f.sql(
            "select id, face_id, person_id, kind, superseded_at from face_assignment where person_id=:p "
            "and kind in ('confirmed','rejected') order by id",
            p=p1,
        ).all()
        == before
    )
    assert f.sql("select display_name, status, is_frozen from person where id=:p", p=p1).one() == named
    assert named.is_frozen is True
    people = f.get("/people")
    assert people["others"] >= 0 and all(p["face_count"] >= 3 for p in people["people"])


@pytest.mark.integration
@pytest.mark.tc("FACE-ASN-006")
def test_asn_006_rejected_person_never_suggested_again(f):
    f.enable()
    ids = _library(f, people=(1,), per=8)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    f.post(f"/faces/{ids[1][0]}/reject", {"person_id": p1})
    f.post("/faces/cluster")
    assert f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar() != p1


@pytest.mark.integration
@pytest.mark.tc("FACE-MGT-001")
def test_mgt_001_merge_and_undo(f):
    f.enable()
    _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    a, b = [p["id"] for p in f.get("/people")["people"]]
    before = f.sql("select id, person_id, state from face order by id").all()
    m = f.post(f"/people/{a}/merge", {"into": b})
    assert f.sql("select count(*) from face where person_id=:a", a=a).scalar() == 0
    assert f.sql("select merged_into from person where id=:a", a=a).scalar() == b
    f.post(f"/people/merges/{m['undo_token']}/undo")
    assert f.sql("select id, person_id, state from face order by id").all() == before
    assert f.sql("select merged_into from person where id=:a", a=a).scalar() is None


@pytest.mark.integration
@pytest.mark.tc("FACE-MGT-002")
def test_mgt_002_split(f):
    f.enable()
    _library(f, people=(1,), per=10)
    f.post("/faces/cluster")
    p1 = f.get("/people")["people"][0]["id"]
    mine = [x["id"] for x in f.get(f"/people/{p1}")["faces"]]
    out = f.post(f"/people/{p1}/split", {"face_ids": mine[:5]})
    new = out["person_id"]
    assert f.sql("select is_frozen from person where id=:p", p=new).scalar() is True
    assert f.sql("select count(*) from face where person_id=:p", p=p1).scalar() == len(mine) - 5
    assert f.sql("select count(*) from face where person_id=:p", p=new).scalar() == 5


@pytest.mark.integration
@pytest.mark.tc("FACE-MGT-004")
def test_mgt_004_ignore_two_ways(f):
    f.enable()
    _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    a, b = [p["id"] for p in f.get("/people")["people"]]
    f.post(f"/people/{a}/ignore", {"delete_biometrics": False})
    f.post(f"/people/{b}/ignore", {"delete_biometrics": True})
    assert [p["id"] for p in f.get("/people")["people"]] == []
    keep = f.sql("select count(*) from face where person_id=:a", a=a).scalar()
    f.post("/faces/cluster")
    assert f.get("/people")["people"] == [] and keep == 6  # kept embeddings stop the group from coming back
    with f.eng.begin() as c:
        c.execute(sqlalchemy.text("SET LOCAL ROLE opagent_bio"))
        n = c.execute(
            sqlalchemy.text(
                "select count(*) from bio.face_embedding e join face x on x.id=e.face_id where x.person_id=:b"
            ),
            {"b": b},
        ).scalar()
    assert n == 0


@pytest.mark.integration
@pytest.mark.tc("FACE-FIND-001", "FACE-FIND-004")
def test_find_001_004_find_by_face_within_the_workspace(f):
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    out = f.post("/faces/find", {"face_ids": [ids[1][0]]})
    sure = {a for a, _ in [(x["asset_id"], x["score"]) for x in out["sure"]]}
    assert len(sure) >= 4 and all(x["score"] >= 0.55 for x in out["sure"])
    assert all(0.42 <= x["score"] < 0.55 for x in out["maybe"])
    owners = {f.sql("select x.asset_id from face x where x.id=:i", i=i).scalar() for i in ids[2]}
    assert not sure & owners
    f.sql("insert into workspace(id, name, settings) values ('w2','Other','{}')")
    r = f.client.post("/api/v1/faces/find", json={"face_ids": [ids[1][0]], "workspace": "w2"}, headers=f.ui)
    assert r.status_code == 200 and r.json() == {"sure": [], "maybe": []}


@pytest.mark.integration
@pytest.mark.tc("FACE-FIND-002")
def test_find_002_query_image_never_lands(f, monkeypatch):
    f.enable()
    _library(f, people=(1,), per=6)
    import opagent_api.faces as faces_mod

    monkeypatch.setattr(faces_mod, "embed_query_via_worker", lambda st, data: f.fb.detect("q/IMG_p1.jpg"))
    secret = b"\xff\xd8query-image-bytes"
    r = f.client.post("/api/v1/faces/query-image", files={"file": ("q.jpg", secret, "image/jpeg")}, headers=f.ui)
    q = r.json()
    assert r.status_code == 200 and len(q["faces"]) == 1 and "vec" not in q["faces"][0]
    out = f.post("/faces/find", {"query": q["handle"], "index": 0})
    assert len(out["sure"]) >= 4
    dump = " ".join(str(x) for x in f.sql("select * from face").all())
    assert "query-image-bytes" not in dump and f.sql("select count(*) from face").scalar() == 6
    f.client.app.state.opa.clock.advance(601)
    r = f.client.post("/api/v1/faces/find", json={"query": q["handle"], "index": 0}, headers=f.ui)
    assert r.status_code == 410 and r.json()["code"] == "QUERY_EXPIRED"


@pytest.mark.integration
@pytest.mark.tc("FACE-TPL-001")
def test_tpl_001_person_fanout(f):
    f.enable()
    f.photo("2025/fam/IMG_p1_p2.jpg")
    for i in range(6):
        f.photo(f"2025/a/IMG_{i}_p1.jpg")
        f.photo(f"2025/b/IMG_{i}_p2.jpg")
    f.add("2025/none/landscape.jpg")
    f.index()
    f.post("/faces/cluster")
    names = {}
    for p in f.get("/people")["people"]:
        rel = f.sql(
            "select l.rel_path from face x join asset_location l on l.asset_id=x.asset_id where "
            "x.person_id=:p and l.rel_path not like '%%p1_p2%%' order by l.rel_path limit 1",
            p=p["id"],
        ).scalar()
        names[p["id"]] = "媽媽" if "_p1" in rel else "爸爸"
        f.post(f"/people/{p['id']}/name", {"name": names[p["id"]]})
    out = f.post(
        "/faces/plan-by-person",
        {
            "target": {"root_alias": "sorted", "sub_path": ""},
            "path_template": "{person}/{stem}{ext}",
            "fallback": "未知人物",
        },
    )
    acts = f.sql("select dst_rel from output_action where plan_id=:p", p=out["plan_id"]).scalars().all()
    assert "媽媽/IMG_p1_p2.jpg" in acts and "爸爸/IMG_p1_p2.jpg" in acts and "未知人物/landscape.jpg" in acts


@pytest.mark.integration
@pytest.mark.tc("FACE-PRG-001", "FACE-PRV-001")
def test_prg_001_purge_everything_in_one_go(f):
    f.enable()
    _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    f.index()
    r = f.client.post("/api/v1/faces/purge", json={"confirm": "wrong"}, headers=f.ui)
    assert r.status_code == 422 and r.json()["code"] == "CONFIRM_TEXT_MISMATCH"
    rep = f.post("/faces/purge", {"confirm": "Default"})
    assert rep["faces"] == 12 and rep["embeddings"] == 12 and rep["persons"] == 2 and "vec" not in str(rep)
    for t in ("face", "face_assignment", "person", "person_link", "face_cluster_run"):
        assert f.sql(f"select count(*) from {t}").scalar() == 0, t
    assert f.sql("select count(*) from pg_tables where schemaname='bio' and tablename like '%_w%'").scalar() == 0
    assert f.sql("select count(*) from search_doc where person_ids is not null").scalar() == 0
    assert f.sql("select count(*) from face_consent where action='purge'").scalar() == 1
    assert f.get("/faces/settings")["enabled"] is False
    from opagent_db.lifecycle import dump_args

    assert "--exclude-schema=bio" in dump_args("pg_dump", "dsn", "out")


@pytest.mark.security
@pytest.mark.tc("FACE-PRV-003")
def test_prv_003_api_role_cannot_read_bio(f):
    f.enable()
    f.photo("IMG_p1.jpg")
    st = f.client.app.state.opa
    with st.engine.begin() as c:
        assert c.execute(sqlalchemy.text("select current_user")).scalar() == "opagent_api"
        assert c.execute(sqlalchemy.text("select has_schema_privilege('opagent_api','bio','USAGE')")).scalar() is False
        assert c.execute(sqlalchemy.text("select pg_has_role('opagent_api','opagent_bio','MEMBER')")).scalar() is False
    for q in ("select 1 from bio.face_embedding limit 1", "create table bio.x(i int)"):
        with pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"), st.engine.begin() as c:
            c.execute(sqlalchemy.text(q))
    from opagent_api.faces import bio_session

    with bio_session(st) as c:
        assert c.execute(sqlalchemy.text("select count(*) from bio.face_embedding")).scalar() == 1
    with st.engine.begin() as c:
        assert c.execute(sqlalchemy.text("select current_user")).scalar() == "opagent_api"


@pytest.mark.security
@pytest.mark.tc("FACE-PRV-003")
def test_prv_003_only_the_face_repository_switches_role():
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    hits = [p.name for p in src.rglob("*.py") if "opagent_bio" in p.read_text(encoding="utf-8")]
    assert hits == ["faces.py"]


@pytest.mark.security
@pytest.mark.tc("FACE-PRV-002")
def test_prv_002_logs_never_hold_vectors(f, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    f.enable()
    _library(f, people=(1,), per=4)
    f.post("/faces/cluster")
    floats = re.compile(r"(-?\d+\.\d+,\s*){63,}")
    assert not floats.search(caplog.text)


@pytest.mark.integration
@pytest.mark.tc("FACE-SPACE-002")
def test_space_002_faces_of_vanished_photos_deleted_after_30_days(f):
    f.enable()
    aid, _ = f.photo("gone/IMG_p1.jpg")
    keep, _ = f.photo("here/IMG_p1.jpg")
    f.sql(
        "update asset_location set missing=true, last_seen_at=:t where asset_id=:a",
        a=aid,
        t=datetime(2026, 9, 1, tzinfo=UTC),
    )
    out = f.post("/faces/maintenance")
    assert out["deleted_faces"] == 1
    assert f.sql("select count(*) from face where asset_id=:a", a=aid).scalar() == 0
    assert f.sql("select count(*) from face where asset_id=:a", a=keep).scalar() == 1


@pytest.mark.integration
@pytest.mark.tc("FACE-SPACE-001")
def test_space_001_model_switch_keeps_human_decisions(f):
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    f.post(f"/people/{p1}/name", {"name": "小明"})
    f.post(f"/faces/{ids[1][1]}/confirm", {"person_id": p1})
    old = f.get("/faces/settings")["space_id"]
    mig = f.post("/faces/space-migrations", {"fingerprint": "fake-face-v2"})
    assert f.post("/faces/find", {"face_ids": [ids[1][0]]})["sure"]  # old results still answer meanwhile
    fb2 = FakeFaceBackend()
    for fid in [x for v in ids.values() for x in v]:
        aid = f.sql("select asset_id from face where id=:i", i=fid).scalar()
        rel = f.sql("select rel_path from asset_location where asset_id=:a", a=aid).scalar()
        f.client.post(
            "/worker-api/v1/faces/reembed",
            json={
                "migration_id": mig["id"],
                "face_id": fid,
                "vec": fb2.sample(int(re.search(r"_p(\d+)", rel).group(1)), "v2" + fid),
            },
            headers=f.ui,
        )
    f.post(f"/faces/space-migrations/{mig['id']}/complete")
    s = f.get("/faces/settings")
    assert s["space_id"] != old
    assert f.sql("select display_name from person where id=:p", p=p1).scalar() == "小明"
    assert (
        f.sql("select kind from face_assignment where face_id=:i and superseded_at is null", i=ids[1][1]).scalar()
        == "confirmed"
    )
    f.client.app.state.opa.clock.advance(8 * 86400)
    f.post("/faces/maintenance")
    from opagent_api.faces import bio_session

    with bio_session(f.client.app.state.opa) as c:
        assert (
            c.execute(sqlalchemy.text("select count(*) from bio.face_embedding where space_id=:s"), {"s": old}).scalar()
            == 0
        )


@pytest.mark.integration
@pytest.mark.tc("FACE-DET-002", "FACE-DET-001")
def test_det_truncated_and_tier0_stored_without_embedding(f):
    f.enable()
    aid, fids = f.photo("crowd.jpg", plan=[FaceSpec(person=1), FaceSpec(person=None, min_side=20)])
    rows = f.sql("select quality_tier from face where asset_id=:a order by quality_tier", a=aid).scalars().all()
    assert rows == [0, 3]
    from opagent_api.faces import bio_session

    with bio_session(f.client.app.state.opa) as c:
        assert c.execute(sqlalchemy.text("select count(*) from bio.face_embedding")).scalar() == 1


@pytest.mark.integration
@pytest.mark.tc("FACE-CLU-005")
def test_clu_005_large_pool_uses_the_knn_graph_path(f, monkeypatch):
    import opagent_api.faces as faces_mod

    monkeypatch.setattr(faces_mod, "LARGE_POOL", 20)  # the 50k path, forced on a small library (timing: nightly)
    f.enable()
    _library(f, people=(1, 2, 3), per=10)
    run = f.post("/faces/cluster")
    assert run["algo"] == "knn_graph+chinese_whispers" and run["clusters"] == 3
    assert f.sql("select algo from face_cluster_run").scalar() == "knn_graph+chinese_whispers"


@pytest.mark.integration
@pytest.mark.tc("FACE-BKL-001")
def test_face_bkl_001_backlog_lists_photos_that_never_had_a_face_step(f):
    """回歸: the face step rides on a job's decode, and a cached photo never reaches it, so turning faces on after
    organizing left the whole library unscanned for good."""

    def backlog(**params):
        return f.client.get("/worker-api/v1/faces/backlog", params=params, headers=f.ui).json()

    a = f.add("old/a.jpg")
    b = f.add("old/b.jpg")
    assert backlog()["items"] == []  # off by default: nothing is even listed (FACE-GATE-001)
    f.enable()
    got = backlog()
    assert {i["asset_id"] for i in got["items"]} == {a, b}
    assert {i["rel"] for i in got["items"]} == {"old/a.jpg", "old/b.jpg"}
    assert all(i["root_alias"] for i in got["items"])
    first = backlog(limit=1)
    assert len(first["items"]) == 1 and first["next"] == first["items"][0]["asset_id"]
    rest = backlog(after=first["next"])
    assert [i["asset_id"] for i in rest["items"]] == [({a, b} - {first["next"]}).pop()]
    done, _ = f.photo("old/p1.jpg")  # a photo that went through a job already has its faces
    assert done not in {i["asset_id"] for i in backlog()["items"]}


@pytest.mark.integration
@pytest.mark.tc("FACE-CLU-010")
def test_face_clu_010_grouping_runs_when_new_faces_are_waiting(f):
    """回歸 (seen on the reference machine): 3,082 faces found, 0 people. Nothing but the tests called the clustering."""
    f.enable()
    _library(f, people=range(1, 4), per=8)

    def due(n):
        return f.client.post("/worker-api/v1/faces/cluster-due", json={"min_new": n}, headers=f.ui).json()

    assert due(1000)["ran"] is False
    r = due(1)
    assert r["ran"] is True and r["clusters"] == 3
    assert due(1)["ran"] is False  # nothing new since that run
    ops = f.get("/operations?status=all&type=face.cluster")
    assert [o["status"] for o in ops] == ["succeeded"]


@pytest.mark.integration
@pytest.mark.tc("FACE-CLU-010")
def test_face_clu_010_off_means_off(f):
    r = f.client.post("/worker-api/v1/faces/cluster-due", json={"min_new": 1}, headers=f.ui).json()
    assert r["ran"] is False


@pytest.mark.integration
@pytest.mark.tc("FACE-CLU-011")
def test_face_clu_011_a_person_has_a_face_to_show(f):
    f.enable()
    _library(f, people=range(1, 3), per=6)
    f.post("/faces/cluster")
    for p in f.get("/people")["people"]:
        assert p["cover"]["asset_id"] and p["cover"]["ch"]


@pytest.mark.integration
@pytest.mark.tc("FACE-DET-010")
def test_face_det_010_analysing_a_photo_again_adds_no_faces(f):
    """回歸 (seen on the reference machine): 1,781 faces stored as 3,868 rows. Three copies of one face are a perfect
    HDBSCAN cluster, so one library became 439 groups, and every copy filled a k-NN slot of a real neighbour."""
    f.enable()
    aid, first = f.photo("2025/1/IMG_0_p1.jpg")
    faces = [{k: v for k, v in x.items() if k != "person_hint"} for x in f.fb.detect("2025/1/IMG_0_p1.jpg")]
    r = f.client.post(
        "/worker-api/v1/faces",
        json={"worker_id": "w1", "asset_id": aid, "fingerprint": f.fb.fingerprint, "truncated": False, "faces": faces},
        headers=f.ui,
    )
    assert r.status_code == 200, r.text
    assert sorted(r.json()["face_ids"]) == sorted(first)
    assert f.sql("select count(*) from face where asset_id=:a", a=aid).scalar() == len(first)
    from opagent_api.faces import bio_session

    with bio_session(f.client.app.state.opa) as c:
        assert c.execute(sqlalchemy.text("select count(*) from bio.face_embedding")).scalar() == len(first)


def _small(f, person, n, tag):
    """Faces too small to be assigned automatically (tier 1): the ones a person has to say yes to."""
    return [
        f.photo(f"2025/x/{tag}_{i}_p{person}.jpg", plan=[FaceSpec(person=person, min_side=40)])[1][0] for i in range(n)
    ]


@pytest.mark.integration
@pytest.mark.tc("FACE-FIND-011")
def test_find_011_who_else_might_this_be(f):
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    more = _small(f, 1, 4, "kid")
    others = _small(f, 2, 3, "other")
    out = f.get(f"/people/{p1}/candidates")
    got = [c["face_id"] for c in out["items"]]
    assert set(more) <= set(got) and not set(others) & set(got)
    assert not set(ids[1]) & set(got)  # already theirs
    c = out["items"][0]
    assert c["asset_id"] and len(c["bbox"]) == 4 and c["score"] >= 0.42
    assert [x["score"] for x in out["items"]] == sorted((x["score"] for x in out["items"]), reverse=True)


@pytest.mark.integration
@pytest.mark.tc("FACE-FIND-012")
def test_find_012_yes_and_no_in_one_go(f):
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    more = _small(f, 1, 4, "kid")
    r = f.post(f"/people/{p1}/review", {"yes": more[:3], "no": more[3:]})
    assert r == {"confirmed": 3, "rejected": 1}
    assert {f.sql("select state from face where id=:i", i=i).scalar() for i in more[:3]} == {"confirmed"}
    assert f.sql("select person_id from face where id=:i", i=more[0]).scalar() == p1
    left = [c["face_id"] for c in f.get(f"/people/{p1}/candidates")["items"]]
    assert not set(more) & set(left)  # yes: theirs now; no: never asked again
    assert f.get("/people")["people"][0]["name"] is None  # a yes is a decision, not a name


@pytest.mark.integration
@pytest.mark.tc("FACE-FIND-013")
def test_find_013_ignored_people_stay_out_of_it(f):
    """回歸 (security review): someone the person chose to ignore must not come back as 「可能也是他」."""
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    p2 = f.sql("select person_id from face where id=:i", i=ids[2][0]).scalar()
    more = _small(f, 1, 2, "kid")
    f.post(f"/people/{p2}/ignore", {"delete_biometrics": False})
    # p2's faces stay out even when they look like p1, and an ignored person has no candidates at all
    f.sql("update face set person_id=:p, state='auto' where id = any(:ids)", p=p2, ids=more)
    assert not set(more) & {c["face_id"] for c in f.get(f"/people/{p1}/candidates")["items"]}
    assert f.get(f"/people/{p2}/candidates")["items"] == []
    r = f.client.post(f"/api/v1/people/{p1}/review", json={"yes": more, "no": []}, headers=f.ui)
    assert r.status_code == 409 and r.json()["code"] == "FACE_IGNORED"


@pytest.mark.integration
@pytest.mark.tc("FACE-CLU-012")
def test_face_clu_012_a_group_shows_its_photos_right_away(f):
    """回歸 (audit round 1): every person's photo grid was empty. Grouping and naming queued the search index update,
    but only a job's results drained that queue, so a library analysed earlier never learned who is in its photos."""
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    r = f.client.post("/api/v1/search", json={"q": None, "filters": {"persons_any": [p1]}, "limit": 50}, headers=f.ui)
    assert r.status_code == 200 and r.json()["total"] == 6
    more = _small(f, 1, 1, "kid")
    f.post(f"/people/{p1}/review", {"yes": more, "no": []})
    r = f.client.post("/api/v1/search", json={"q": None, "filters": {"persons_any": [p1]}, "limit": 50}, headers=f.ui)
    assert r.json()["total"] == 7


@pytest.mark.integration
@pytest.mark.tc("VIEW-021")
def test_view_021_who_is_in_this_photo(f):
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    aid = f.sql("select asset_id from face where id=:i", i=ids[1][0]).scalar()
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    f.post(f"/people/{p1}/name", {"name": "小明"})
    assert f.get(f"/assets/{aid}")["people"] == [{"id": p1, "name": "小明"}]
    f.post(f"/people/{p1}/ignore", {"delete_biometrics": False})
    assert f.get(f"/assets/{aid}")["people"] == []


@pytest.mark.integration
@pytest.mark.tc("FACE-UI-012")
def test_face_ui_012_people_in_the_order_people_expect(f):
    """回歸 (audit round 1): cards showed photo counts but were sorted by face count (… 5, 4, 5 …)."""
    f.enable()
    ids = _library(f, people=(1, 2, 3), per=5)
    f.post("/faces/cluster")
    pid = {n: f.sql("select person_id from face where id=:i", i=ids[n][0]).scalar() for n in (1, 2, 3)}
    for i in range(3):  # person 1: three more faces in photos they are already in -> most faces, 5 photos
        f.sql(
            "insert into face(id, workspace_id, asset_id, space_id, bbox, det_score, quality, quality_tier, state, person_id) "
            "select id || :n, workspace_id, asset_id, space_id, bbox, det_score, quality, 3, 'auto', person_id from face where id = :i",
            n=str(i),
            i=ids[1][0],
        )
    f.photo("2025/3/IMG_9_p3.jpg")  # person 3: a sixth photo, joins them online
    f.post(f"/people/{pid[2]}/name", {"name": "小華"})
    people = f.get("/people")["people"]
    assert people[0]["name"] == "小華"  # named first
    assert [p["id"] for p in people[1:3]] == [pid[3], pid[1]]  # then by photos: 6 before 5, whatever the faces


@pytest.mark.integration
@pytest.mark.tc("VIEW-025")
def test_view_025_the_people_of_a_photo_show_their_faces(f):
    """回歸 (audit round 2): 「照片中的人物」 showed a bare 「未命名」 with no face to tell who it is."""
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    aid = f.sql("select asset_id from face where id=:i", i=ids[1][0]).scalar()
    d = f.get(f"/assets/{aid}")
    (p,) = d["people"]
    assert len(d["people_faces"][p["id"]]["bbox"]) == 4 and d["people_faces"][p["id"]]["ch"]


@pytest.mark.integration
@pytest.mark.tc("FACE-MGT-010")
def test_mgt_010_the_likeliest_pairs_first_and_no_means_no(f):
    """回歸 (reference library): 1,101 merge suggestions among 52 groups, none shown anywhere. The page asks about the
    likeliest few; 「不是同一人」 closes the pair for good."""
    f.enable()
    _library(f, people=(1, 2, 3), per=6)
    f.post("/faces/cluster")
    ids = [p["id"] for p in f.get("/people")["people"]]
    with f.eng.begin() as c:
        for (a, b), sc in zip([(ids[0], ids[1]), (ids[0], ids[2]), (ids[1], ids[2])], (0.6, 0.9, 0.7), strict=True):
            c.execute(
                sqlalchemy.text(
                    "insert into person_link(workspace_id, person_a, person_b, kind, score) values ('default', :a, :b,"
                    " 'merge_suggestion', :s) on conflict (workspace_id, person_a, person_b, kind) do update set score = :s"
                ),
                {"a": a, "b": b, "s": sc},
            )
    sug = f.get("/people")["merge_suggestions"]
    assert [round(s["score"], 2) for s in sug[:3]] == [0.9, 0.7, 0.6]
    top = sug[0]
    f.post("/people/merge-suggestions/dismiss", {"a": top["a"], "b": top["b"]})
    assert (top["a"], top["b"]) not in {(s["a"], s["b"]) for s in f.get("/people")["merge_suggestions"]}


@pytest.mark.integration
@pytest.mark.tc("FACE-MGT-011")
def test_mgt_011_suggestions_about_people_that_are_gone_are_not_shown(f):
    """回歸 (audit round 3): the 「這兩組是同一個人嗎？」 card never appeared: the top pairs named groups a re-grouping
    had deleted (person_link has no cascade), so the page found nobody to show."""
    f.enable()
    _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    ids = [p["id"] for p in f.get("/people")["people"]]
    with f.eng.begin() as c:
        c.execute(
            sqlalchemy.text(
                "insert into person_link(workspace_id, person_a, person_b, kind, score) values"
                " ('default', 'gone-1', 'gone-2', 'merge_suggestion', 0.99), ('default', :a, :b, 'merge_suggestion', 0.7)"
                " on conflict (workspace_id, person_a, person_b, kind) do update set score = excluded.score"
            ),
            {"a": ids[0], "b": ids[1]},
        )
    sug = f.get("/people")["merge_suggestions"]
    assert [(s["a"], s["b"]) for s in sug] == [(ids[0], ids[1])]
    f.post("/faces/cluster")
    assert f.sql("select count(*) from person_link where person_a = 'gone-1'").scalar() == 0


@pytest.mark.integration
@pytest.mark.tc("VIEW-027")
def test_view_027_a_photo_says_when_its_folder_is_not_checked_for_faces(f):
    """回歸 (audit round 5): group photos of the 「photos」 folder had no boxes and no people, with nothing saying that
    faces were turned on for the family folders only."""
    f.enable()
    aid, _ = f.photo("2025/1/IMG_0_p1.jpg")
    assert f.get(f"/assets/{aid}")["faces_state"] == "on"
    f.sql("update workspace set settings = jsonb_set(settings, '{faces,sources}', '[\"elsewhere\"]')")
    assert f.get(f"/assets/{aid}")["faces_state"] == "folder_off"


@pytest.mark.integration
@pytest.mark.tc("FACE-FIND-016")
def test_find_016_the_last_review_can_be_taken_back(f):
    """回歸 (audit round 5): 「全部都是」 then 送出 moved a dozen faces to a person with no way back."""
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    more = _small(f, 1, 4, "kid")
    before = {i: f.sql("select person_id, state from face where id=:i", i=i).one() for i in more}
    f.post(f"/people/{p1}/review", {"yes": more[:3], "no": more[3:]})
    r = f.post(f"/people/{p1}/review/undo", {})
    assert r == {"restored": 4}
    assert {i: f.sql("select person_id, state from face where id=:i", i=i).one() for i in more} == before
    got = {c["face_id"] for c in f.get(f"/people/{p1}/candidates")["items"]}
    assert set(more) <= got  # the 「不是」 is gone too: they are asked again
    r = f.client.post(f"/api/v1/people/{p1}/review/undo", json={}, headers=f.ui)
    assert r.status_code == 404  # only once


@pytest.mark.integration
@pytest.mark.tc("FACE-MGT-013")
def test_mgt_013_a_person_lists_faces_to_compare(f):
    """回歸 (audit round 5): 「可能是同一人？」 showed one face per group, too little to decide on."""
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    fs = f.get(f"/people/{p1}")["faces"]
    assert len(fs) == 6 and all(len(x["bbox"]) == 4 and x["ch"] for x in fs)


@pytest.mark.integration
@pytest.mark.tc("VIEW-028")
def test_view_028_a_photo_says_its_faces_belong_to_nobody_yet(f):
    """回歸 (audit round 6): a team photo in 「photos」 had 12 faces found, all too small or of people seen only once,
    and the info panel said nothing about people at all."""
    f.enable()
    aid, _ = f.photo("2025/t/team.jpg", plan=[FaceSpec(person=None, min_side=12), FaceSpec(person=None)])
    f.post("/faces/cluster")
    a = f.get(f"/assets/{aid}")
    assert a["people"] == [] and a["faces_found"] == {"total": 2, "small": 1}


@pytest.mark.integration
@pytest.mark.tc("VIEW-029")
def test_view_029_the_faces_of_nobody_are_marked_too(f):
    """回歸 (audit round 7): 「找到 11 張臉」 with no box on the photo; nobody could see which faces it meant."""
    f.enable()
    aid, _ = f.photo(
        "2025/t/team2.jpg",
        plan=[FaceSpec(person=None, min_side=12, bbox=(0.1, 0.1, 0.05, 0.05)), FaceSpec(person=None)],
    )
    f.post("/faces/cluster")
    got = sorted(f.get(f"/assets/{aid}")["unknown_faces"], key=lambda x: x["small"])
    assert [x["small"] for x in got] == [False, True]
    assert got[1]["bbox"] == pytest.approx([0.1, 0.1, 0.05, 0.05])


@pytest.mark.integration
@pytest.mark.tc("FACE-FIND-018")
def test_find_018_a_candidate_says_how_clear_it_is(f):
    """回歸 (audit round 7): about 1 in 4 candidates was wrong, mostly blurry crops; each one now carries its quality
    tier so the page can say which are hard to judge."""
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    _small(f, 1, 2, "kid")
    items = f.get(f"/people/{p1}/candidates")["items"]
    assert items and all(x["tier"] in (1, 2, 3) for x in items)


@pytest.mark.integration
@pytest.mark.tc("FACE-FIND-019")
def test_find_019_hard_candidates_say_so_and_come_last(f):
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    small = _small(f, 1, 2, "kid")  # min_side 40
    items = f.get(f"/people/{p1}/candidates")["items"]
    by = {x["face_id"]: x for x in items}
    assert all(by[i]["clear"] is False for i in small if i in by) and any(i in by for i in small)
    flags = [x["clear"] for x in items]
    assert flags == sorted(flags, reverse=True)  # the clear ones first


@pytest.mark.integration
@pytest.mark.tc("FACE-UI-017")
def test_face_ui_017_grouping_picks_each_cover(f):
    f.enable()
    _library(f, people=range(1, 3), per=6)
    f.post("/faces/cluster")
    for p in f.get("/people")["people"]:
        cf = f.sql("select cover_face_id from person where id=:i", i=p["id"]).scalar()
        assert cf is not None
        assert f.sql("select person_id from face where id=:i", i=cf).scalar() == p["id"]
        assert p["cover"]["asset_id"] == f.sql("select asset_id from face where id=:i", i=cf).scalar()


@pytest.mark.integration
@pytest.mark.tc("FACE-UI-017")
def test_face_ui_017_a_library_grouped_before_gets_its_covers(f):
    """A library grouped by an older version has no cover_face_id; the people page fills them in once."""
    f.enable()
    _library(f, people=range(1, 3), per=6)
    f.post("/faces/cluster")
    f.sql("update person set cover_face_id = null")
    f.get("/people")
    assert f.sql("select count(*) from person where cover_face_id is null and merged_into is null").scalar() == 0


@pytest.mark.integration
@pytest.mark.tc("FACE-FIND-020")
def test_find_020_candidates_say_which_are_sure(f):
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    _small(f, 1, 2, "kid")
    items = f.get(f"/people/{p1}/candidates")["items"]
    assert items and all(x["sure"] is (x["score"] >= 0.6) for x in items)


@pytest.mark.integration
@pytest.mark.tc("FACE-ASN-007")
def test_asn_007_a_small_face_is_only_suggested_for_a_named_person(f):
    """ADR-0036: a 24-31 px face is embedded, but only a person's yes puts it anywhere, and only for someone named."""
    f.enable()
    ids = _library(f, people=(1, 2), per=6)
    f.post("/faces/cluster")
    p1 = f.sql("select person_id from face where id=:i", i=ids[1][0]).scalar()
    _, (tiny,) = f.photo("2025/s/tiny_a_p1.jpg", plan=[FaceSpec(person=1, min_side=28)])
    assert f.sql("select state from face where id=:i", i=tiny).scalar() == "unassigned"
    f.post(f"/people/{p1}/name", {"name": "小明"})
    _, (tiny2,) = f.photo("2025/s/tiny_b_p1.jpg", plan=[FaceSpec(person=1, min_side=28)])
    assert f.sql("select state from face where id=:i", i=tiny2).scalar() == "suggested"
    assert f.sql("select person_id from face where id=:i", i=tiny2).scalar() is None  # never automatic


@pytest.mark.integration
@pytest.mark.tc("FACE-DET-013")
def test_det_013_faces_stored_too_small_are_embedded_again(f):
    """ADR-0036: faces stored as tier 0 at 24-31 px get an embedding from the worker, once."""
    f.enable()
    aid, (fid,) = f.photo("2025/s/team_p1.jpg", plan=[FaceSpec(person=1, min_side=28)])
    # as an older version stored it: tier 0, no embedding
    f.sql("update face set quality_tier = 0 where id=:i", i=fid)
    f.sql("delete from bio.face_embedding where face_id=:i", i=fid)
    r = f.client.get("/worker-api/v1/faces/reembed", params={"limit": 10}, headers=f.ui).json()
    assert [x["face_id"] for it in r["items"] for x in it["faces"]] == [fid]
    assert r["items"][0]["asset_id"] == aid and r["items"][0]["rel"]
    vec = [1.0] + [0.0] * 511
    ok = f.client.post(f"/worker-api/v1/faces/{fid}/embedding", json={"vec": vec, "quality_tier": 1}, headers=f.ui)
    assert ok.status_code == 200, ok.text
    assert f.sql("select quality_tier from face where id=:i", i=fid).scalar() == 1
    assert f.sql("select count(*) from bio.face_embedding where face_id=:i", i=fid).scalar() == 1
    assert f.client.get("/worker-api/v1/faces/reembed", params={"limit": 10}, headers=f.ui).json()["items"] == []
    # no match in the photo any more: tried once, not listed again
    _, (fid2,) = f.photo("2025/s/team2_p1.jpg", plan=[FaceSpec(person=1, min_side=28)])
    f.sql("update face set quality_tier = 0 where id=:i", i=fid2)
    f.sql("delete from bio.face_embedding where face_id=:i", i=fid2)
    f.client.post(f"/worker-api/v1/faces/{fid2}/embedding", json={"vec": None}, headers=f.ui)
    assert f.client.get("/worker-api/v1/faces/reembed", params={"limit": 10}, headers=f.ui).json()["items"] == []


@pytest.mark.integration
@pytest.mark.tc("FACE-DET-014")
def test_det_014_a_late_embedding_respects_consent_eligibility_and_decisions(f):
    """回歸 (security review, ADR-0036): the re-embed answer wrote a vector without checking faces were still on, nor
    that the face was one the backlog listed, and could move a face a person had decided on."""
    f.enable()
    vec = [1.0] + [0.0] * 511
    post = lambda fid: f.client.post(f"/worker-api/v1/faces/{fid}/embedding", json={"vec": vec}, headers=f.ui)
    _, (big,) = f.photo("2025/s/big_p1.jpg", plan=[FaceSpec(person=1)])
    assert post(big).status_code == 409  # not on the backlog: a normal face with its own vector
    _, (dec,) = f.photo("2025/s/dec_p1.jpg", plan=[FaceSpec(person=1, min_side=28)])
    f.sql("update face set quality_tier = 0, state = 'confirmed' where id=:i", i=dec)
    f.sql("delete from bio.face_embedding where face_id=:i", i=dec)
    listed = [
        x["face_id"]
        for it in f.client.get("/worker-api/v1/faces/reembed", headers=f.ui).json()["items"]
        for x in it["faces"]
    ]
    assert dec not in listed and post(dec).status_code == 409  # a person's decision is not reopened
    _, (off,) = f.photo("2025/s/off_p1.jpg", plan=[FaceSpec(person=1, min_side=28)])
    f.sql("update face set quality_tier = 0 where id=:i", i=off)
    f.sql("delete from bio.face_embedding where face_id=:i", i=off)
    f.sql("update workspace set settings = jsonb_set(settings, '{faces,enabled}', 'false')")
    assert post(off).status_code == 409
    assert f.sql("select count(*) from bio.face_embedding where face_id = any(:ids)", ids=[dec, off]).scalar() == 0
