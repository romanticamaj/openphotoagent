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
