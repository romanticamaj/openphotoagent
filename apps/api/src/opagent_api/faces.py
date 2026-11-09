"""Faces (07): opt-in, ingestion and online assignment, clustering, person management, find this person, purge.

Biometric vectors live only in the ``bio`` schema, owned by the ``opagent_bio`` role. The API's connections run as
``opagent_api``, which has no access to ``bio``; this module alone switches role, per transaction, through
``bio_session`` (ADR-0011: a guard against accidental queries and leaks, not a boundary against local code).
Detection and embedding happen only on the Worker (invariant 1); here is only vector arithmetic.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import secrets
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from opagent_core import faces as F
from opagent_core.ids import uuid7
from opagent_db.schema import (
    asset,
    asset_location,
    audit_log,
    embedding_space,
    face,
    face_assignment,
    face_cluster_run,
    face_consent,
    index_outbox,
    person,
    person_link,
    search_doc,
    source,
    worker_task,
    workspace,
)
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, insert, select, text, update

from .approvals import ui_only
from .state import DEFAULT_WS, AppState

router = APIRouter()
worker_router = APIRouter()
BIO_ROLE = "opagent_bio"
API_ROLE = "opagent_api"
QUERY_TTL_S = 600
SPACE_RETAIN = timedelta(days=7)
VANISHED_AFTER = timedelta(days=30)
K = 16
LARGE_POOL = 50_000  # above this, HDBSCAN gives way to a k-NN graph + Chinese Whispers (07 §2.3)


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


# ---------------------------------------------------------------- roles (ADR-0011)
ROLE_DDL = f"""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{API_ROLE}') THEN CREATE ROLE {API_ROLE} NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{BIO_ROLE}') THEN CREATE ROLE {BIO_ROLE} NOLOGIN; END IF;
END $$;
GRANT USAGE ON SCHEMA public TO {API_ROLE}, {BIO_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES ON ALL TABLES IN SCHEMA public TO {API_ROLE};
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {API_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {BIO_ROLE};
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {BIO_ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES ON TABLES
  TO {API_ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {API_ROLE};
ALTER SCHEMA bio OWNER TO {BIO_ROLE};
ALTER TABLE bio.face_embedding OWNER TO {BIO_ROLE};
ALTER TABLE bio.person_prototype OWNER TO {BIO_ROLE};
REVOKE ALL ON SCHEMA bio FROM PUBLIC;
REVOKE ALL ON SCHEMA bio FROM {API_ROLE};
"""  # noqa: S608  # nosec B608 - module constants only, no input


def install_roles(engine: Any) -> None:
    """Called once at start-up with the bootstrap (owner) connection, before the API role hook is installed."""
    with engine.begin() as c:
        c.execute(text("SELECT pg_advisory_xact_lock(4242)"))
        c.execute(text(ROLE_DDL))


def api_role_hook(engine: Any) -> None:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_role(dbapi_conn: Any, _rec: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute(f"SET ROLE {API_ROLE}")
        cur.close()
        dbapi_conn.commit()


@contextlib.contextmanager
def bio_session(st: AppState) -> Iterator[Any]:
    """The only way to touch ``bio``: one transaction as ``opagent_bio``; afterwards the session is the API role."""
    with st.engine.begin() as c:
        c.execute(text(f"SET LOCAL ROLE {BIO_ROLE}"))
        yield c


def _part(ws: str) -> str:
    return "w" + hashlib.sha256(ws.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- settings
def fsettings(c: Any, ws: str = DEFAULT_WS) -> dict[str, Any]:
    s = c.execute(select(workspace.c.settings).where(workspace.c.id == ws)).scalar() or {}
    return dict(s.get("faces") or {})


def _put(c: Any, ws: str, faces: dict[str, Any]) -> None:
    cur = c.execute(select(workspace.c.settings).where(workspace.c.id == ws)).scalar() or {}
    c.execute(update(workspace).where(workspace.c.id == ws).values(settings={**cur, "faces": faces}))


def enabled_for(c: Any, source_id: str | None, ws: str = DEFAULT_WS) -> bool:
    s = fsettings(c, ws)
    if not s.get("enabled"):
        return False
    srcs = s.get("sources")
    return srcs is None or source_id in srcs


@router.get("/faces/settings")
def get_settings(req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        s = fsettings(c)
        pending = c.execute(
            select(func.count())
            .select_from(worker_task)
            .where(worker_task.c.kind == "face_purge", worker_task.c.status.in_(("pending", "leased")))
        ).scalar_one()
    with _st(req).engine.begin() as c:
        name = c.execute(select(workspace.c.name).where(workspace.c.id == DEFAULT_WS)).scalar()
    return {
        "enabled": bool(s.get("enabled")),
        "prompt_dismissed": bool(s.get("prompt_dismissed")),
        "workspace_name": name,
        "sources": s.get("sources"),
        "space_id": s.get("space_id"),
        "show_people": bool(s.get("enabled")),
        "worker_purge_pending": bool(pending),
    }


@router.post("/faces/prompt/dismiss")
def dismiss_prompt(req: Request) -> dict[str, Any]:
    """「稍後」 on the opt-in card: never asked again on the photos page; Settings still has the switch."""
    ui_only(req)
    st = _st(req)
    with st.engine.begin() as c:
        _put(c, DEFAULT_WS, {**fsettings(c), "prompt_dismissed": True})
    return get_settings(req)


class DisableIn(BaseModel):
    delete: bool = True  # the UI ticks 「同時刪除」 by default (07 §6.3; FACE-PRG-004)
    confirm: str | None = None


@router.post("/faces/disable")
def disable(body: DisableIn, req: Request) -> dict[str, Any]:
    ui_only(req)
    st = _st(req)
    if body.delete:
        return {"purged": purge(PurgeIn(confirm=body.confirm or ""), req)}
    with st.engine.begin() as c:
        _put(c, DEFAULT_WS, {**fsettings(c), "enabled": False})
        c.execute(
            insert(face_consent).values(
                id=uuid7(st.clock),
                workspace_id=DEFAULT_WS,
                user_id="local",
                action="disable",
                consent_text_version="-",
                at=st.clock.now(),
            )
        )
    return {"purged": None}


class EnableIn(BaseModel):
    consent: bool
    consent_text_version: str
    sources: list[str] | None = None


@router.post("/faces/enable")
def enable(body: EnableIn, req: Request) -> dict[str, Any]:
    ui_only(req)
    if not body.consent:
        raise HTTPException(422, detail={"code": "FACE_CONSENT_REQUIRED"})
    st = _st(req)
    with st.engine.begin() as c:
        c.execute(
            insert(face_consent).values(
                id=uuid7(st.clock),
                workspace_id=DEFAULT_WS,
                user_id="local",
                action="enable",
                consent_text_version=body.consent_text_version,
                source_ids=body.sources,
                at=st.clock.now(),
            )
        )
        _put(c, DEFAULT_WS, {**fsettings(c), "enabled": True, "sources": body.sources})
    _ensure_partitions(st, DEFAULT_WS)
    return get_settings(req)


def _ensure_partitions(st: AppState, ws: str) -> None:
    p = _part(ws)
    with bio_session(st) as c:
        for t in ("face_embedding", "person_prototype"):
            lit = ws.replace("'", "''")  # DDL takes no parameters; workspace ids are app-generated
            c.execute(text(f"CREATE TABLE IF NOT EXISTS bio.{t}_{p} PARTITION OF bio.{t} FOR VALUES IN ('{lit}')"))


def _space(c: Any, fingerprint: str) -> int:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    c.execute(
        pg_insert(embedding_space)
        .values(model_key=fingerprint, fingerprint=fingerprint, dim=512, modality="face", thresholds=F.THRESHOLDS)
        .on_conflict_do_nothing(index_elements=["fingerprint"])
    )
    return int(c.execute(select(embedding_space.c.id).where(embedding_space.c.fingerprint == fingerprint)).scalar_one())


# ---------------------------------------------------------------- vectors
def _vec(s: str | list[float]) -> list[float]:
    if isinstance(s, list):
        return [float(x) for x in s]
    return [float(x) for x in s.strip("[]").split(",")]


def _cos(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


def _vectors(st: AppState, ws: str, space: int, face_ids: list[str] | None = None) -> dict[str, list[float]]:
    q = "SELECT face_id, vec::text FROM bio.face_embedding WHERE workspace_id = :ws AND space_id = :s"
    params: dict[str, Any] = {"ws": ws, "s": space}
    if face_ids is not None:
        if not face_ids:
            return {}
        q += " AND face_id = ANY(:ids)"
        params["ids"] = face_ids
    with bio_session(st) as c:
        return {fid: _vec(v) for fid, v in c.execute(text(q), params).all()}


def _knn(st: AppState, ws: str, space: int, vec: list[float], k: int, exclude: str | None = None) -> list[Any]:
    with bio_session(st) as c:
        return list(
            c.execute(
                text(
                    "SELECT face_id, 1 - (vec <=> CAST(:q AS vector)) AS s FROM bio.face_embedding "
                    "WHERE workspace_id = :ws AND space_id = :sp AND face_id <> :ex "
                    "ORDER BY vec <=> CAST(:q AS vector), face_id LIMIT :k"
                ),
                {"q": str(vec), "ws": ws, "sp": space, "ex": exclude or "-", "k": k},
            ).all()
        )


# ---------------------------------------------------------------- ingestion + online assignment
class FaceIn(BaseModel):
    bbox: list[float]
    landmarks: list[float] | None = None
    det_score: float
    quality: dict[str, Any]
    quality_tier: int
    vec: list[float] | None = None


class FacesIn(BaseModel):
    worker_id: str
    asset_id: str
    fingerprint: str
    truncated: bool = False
    faces: list[FaceIn]


@worker_router.get("/faces/plan")
def face_plan(asset_id: str, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        sid = c.execute(select(asset_location.c.source_id).where(asset_location.c.asset_id == asset_id)).scalar()
        return {"run": enabled_for(c, sid)}


@worker_router.get("/faces/backlog")
def backlog(req: Request, after: str = "", limit: int = 32) -> dict[str, Any]:
    """Photos with no face step yet. It rides on a job's decode, and a cached photo never reaches that, so a library
    organized before faces were turned on is scanned from here, when the worker is idle. Ordered by asset id: the
    worker keeps the cursor, so a photo with no faces in it is not listed forever."""
    with _st(req).engine.begin() as c:
        s = fsettings(c)
        if not s.get("enabled"):
            return {"items": [], "next": after}
        q = (
            select(
                asset_location.c.asset_id, asset_location.c.rel_path, asset_location.c.source_id, source.c.root_alias
            )
            .join(source, source.c.id == asset_location.c.source_id)
            .where(asset_location.c.missing.is_(False), asset_location.c.asset_id > after)
            .order_by(asset_location.c.asset_id)
            .limit(limit)
        )
        rows = c.execute(q).all()
        seen = {r for (r,) in c.execute(select(face.c.asset_id).where(face.c.asset_id.in_([r.asset_id for r in rows])))}
        items = [
            {"asset_id": r.asset_id, "root_alias": r.root_alias, "rel": r.rel_path}
            for r in rows
            if r.asset_id not in seen and enabled_for(c, r.source_id)
        ]
    return {"items": items, "next": rows[-1].asset_id if rows else after}


@worker_router.post("/faces")
def ingest(body: FacesIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        s = fsettings(c)
        if not s.get("enabled"):
            raise HTTPException(409, detail={"code": "FACES_DISABLED"})
        sid = c.execute(select(asset_location.c.source_id).where(asset_location.c.asset_id == body.asset_id)).scalar()
        if not enabled_for(c, sid):
            raise HTTPException(409, detail={"code": "FACES_NOT_FOR_SOURCE"})
        space = s.get("space_id") or _space(c, body.fingerprint)
        if not s.get("space_id"):
            _put(c, DEFAULT_WS, {**s, "space_id": space})
        # a photo is analysed again and again; the job and the idle backlog can also meet on it, hence the row lock
        c.execute(select(asset.c.id).where(asset.c.id == body.asset_id).with_for_update())
        known = c.execute(
            select(face.c.id).where(
                face.c.asset_id == body.asset_id, face.c.space_id == space, face.c.source == "detected"
            )
        )
        existing = [r[0] for r in known]
        if existing:
            return {"face_ids": existing, "truncated": False}
        kept, truncated = F.cap_faces([f.model_dump() for f in body.faces])
        ids = []
        for f in kept:
            fid = uuid7(st.clock)
            ids.append(fid)
            c.execute(
                insert(face).values(
                    id=fid,
                    workspace_id=DEFAULT_WS,
                    asset_id=body.asset_id,
                    space_id=space,
                    bbox=f["bbox"],
                    landmarks=f["landmarks"],
                    det_score=f["det_score"],
                    quality={**f["quality"], "truncated": truncated or body.truncated},
                    quality_tier=f["quality_tier"],
                )
            )
    with bio_session(st) as c:
        for fid, f in zip(ids, kept, strict=True):
            if f["vec"] is not None and f["quality_tier"] >= 1:  # tier 0: bbox only (FACE-DET-001)
                c.execute(
                    text("INSERT INTO bio.face_embedding(workspace_id, face_id, space_id, vec) VALUES (:w,:f,:s,:v)"),
                    {"w": DEFAULT_WS, "f": fid, "s": space, "v": str(f["vec"])},
                )
    for fid, f in zip(ids, kept, strict=True):
        if f["vec"] is not None and f["quality_tier"] >= 1:
            assign_online(st, DEFAULT_WS, space, fid, f["vec"], f["quality_tier"])
    with st.engine.begin() as c:
        c.execute(insert(index_outbox).values(asset_id=body.asset_id))
    return {"face_ids": ids, "truncated": truncated}


def _current(c: Any, face_ids: list[str]) -> dict[str, Any]:
    rows = c.execute(
        select(face_assignment).where(
            face_assignment.c.face_id.in_(face_ids), face_assignment.c.superseded_at.is_(None)
        )
    ).all()
    return {r.face_id: r for r in rows if r.kind != "rejected"}


def _write(c: Any, st: AppState, fid: str, pid: str | None, kind: str, method: str, actor: str, **kw: Any) -> None:
    """Supersede the face's current (non-rejection) assignment and record a new one; keep ``face`` in step."""
    now = st.clock.now()
    cur = c.execute(
        select(face_assignment.c.id).where(
            face_assignment.c.face_id == fid,
            face_assignment.c.superseded_at.is_(None),
            face_assignment.c.kind != "rejected",
        )
    ).scalar()
    if cur is not None:
        c.execute(update(face_assignment).where(face_assignment.c.id == cur).values(superseded_at=now))
    if pid is not None:
        c.execute(
            insert(face_assignment).values(
                id=uuid7(st.clock),
                face_id=fid,
                person_id=pid,
                kind=kind,
                method=method,
                actor=actor,
                created_at=now,
                replaces=cur,
                **kw,
            )
        )
    state = {"auto": "auto", "suggested": "suggested", "confirmed": "confirmed"}.get(kind, "unassigned")
    c.execute(
        update(face)
        .where(face.c.id == fid)
        .values(person_id=pid if state in ("auto", "confirmed") else None, state=state, confidence=kw.get("confidence"))
    )


def assign_online(st: AppState, ws: str, space: int, fid: str, vec: list[float], tier: int) -> F.Decision:
    near = _knn(st, ws, space, vec, K, exclude=fid)
    with st.engine.begin() as c:
        owners = dict(
            c.execute(
                select(face.c.id, face.c.person_id).where(
                    face.c.id.in_([n.face_id for n in near] or ["-"]),
                    face.c.state.in_(("auto", "confirmed")),
                    face.c.person_id.is_not(None),
                )
            ).all()
        )
        live = {
            r.id: r
            for r in c.execute(
                select(person).where(
                    person.c.id.in_(set(owners.values()) or {"-"}),
                    person.c.merged_into.is_(None),
                    person.c.status != "ignored",
                )
            ).all()
        }
        rejected = {
            r[0]
            for r in c.execute(
                select(face_assignment.c.person_id).where(
                    face_assignment.c.face_id == fid, face_assignment.c.kind == "rejected"
                )
            ).all()
        }
        scores: dict[str, list[float]] = {}
        for n in near:
            p = owners.get(n.face_id)
            if p is not None and p in live:
                scores.setdefault(p, []).append(float(n.s))
        links = c.execute(
            select(person_link.c.person_a, person_link.c.person_b).where(person_link.c.kind == "confusable")
        )
        d = F.decide_assignment(
            scores,
            neighbours_of_p1=0,
            tier=tier,
            rejected=rejected,
            age_variation={p for p, r in live.items() if r.age_variation},
            confusable={frozenset(x) for x in links.all()},
        )
        if d.person is not None:
            d = F.decide_assignment(
                scores,
                neighbours_of_p1=len(scores.get(d.person, [])),
                tier=tier,
                rejected=rejected,
                age_variation={p for p, r in live.items() if r.age_variation},
                confusable={
                    frozenset(x)
                    for x in c.execute(
                        select(person_link.c.person_a, person_link.c.person_b).where(person_link.c.kind == "confusable")
                    ).all()
                },
            )
            _write(
                c,
                st,
                fid,
                d.person,
                d.kind,
                "online_knn",
                "system",
                confidence=d.score,
                margin=d.margin,
                space_id=space,
            )
    return d


# ---------------------------------------------------------------- clustering (07 §2.3)
@router.post("/faces/cluster")
def cluster(req: Request) -> dict[str, Any]:
    return run_cluster(_st(req), DEFAULT_WS)


class ClusterDueIn(BaseModel):
    min_new: int = 1


CLUSTER_BATCH = 200  # 07 §2.3: a batch run once the unassigned pool has grown by this much


@worker_router.post("/faces/cluster-due")
def cluster_due(body: ClusterDueIn, req: Request) -> dict[str, Any]:
    """The idle worker asks; faces found since the last run decide. A long run, so it is an Operation (invariant 9)."""
    from .operations import ensure_operation, set_op_status

    st = _st(req)
    with st.engine.begin() as c:
        if not fsettings(c).get("enabled"):
            return {"ran": False, "new": 0}
        last = c.execute(select(func.max(face_cluster_run.c.id)).where(face_cluster_run.c.workspace_id == DEFAULT_WS))
        since = last.scalar() or ""
        new = c.execute(
            select(func.count())
            .select_from(face)
            .where(
                face.c.workspace_id == DEFAULT_WS,
                face.c.quality_tier >= 2,
                face.c.state == "unassigned",
                face.c.id > since,
            )
        ).scalar_one()
        if new < body.min_new:
            return {"ran": False, "new": int(new)}
        oid = uuid7(st.clock)
        ensure_operation(c, st.clock, oid, "face.cluster", {"kind": "faces", "id": DEFAULT_WS})
        set_op_status(c, st.clock, oid, "running")
    try:
        out = run_cluster(st, DEFAULT_WS)
    except Exception:
        with st.engine.begin() as c:
            set_op_status(c, st.clock, oid, "failed")
        raise
    with st.engine.begin() as c:
        set_op_status(c, st.clock, oid, "succeeded")
    return {"ran": True, **out}


def run_cluster(st: AppState, ws: str) -> dict[str, Any]:
    import numpy as np
    from sklearn.cluster import HDBSCAN

    with st.engine.begin() as c:
        s = fsettings(c, ws)
        space = s.get("space_id")
        if not s.get("enabled") or space is None:
            return {"clusters": 0, "noise": 0, "input_faces": 0}
        pool = [
            r.id
            for r in c.execute(
                select(face.c.id).where(
                    face.c.workspace_id == ws, face.c.quality_tier >= 2, face.c.state.in_(("unassigned",))
                )
            ).all()
        ]
        # faces of ignored persons that kept their embedding pull a stranger back into that person, not a group
        ignored_faces = {
            r[0]
            for r in c.execute(
                select(face.c.id).join(person, person.c.id == face.c.person_id).where(person.c.status == "ignored")
            ).all()
        }
        soft: dict[str, set[str]] = {
            r.id: set()
            for r in c.execute(
                select(person).where(
                    person.c.workspace_id == ws,
                    person.c.is_frozen.is_(False),
                    person.c.origin == "cluster",
                    person.c.merged_into.is_(None),
                    person.c.status != "ignored",
                )
            ).all()
        }
        for fid, pid in c.execute(select(face.c.id, face.c.person_id).where(face.c.person_id.in_(soft or {"-"}))):
            soft[pid].add(fid)
        pool += [f for fs in soft.values() for f in fs]
    vecs = _vectors(st, ws, space, sorted(set(pool)))
    ids = [f for f in sorted(set(pool)) if f in vecs and f not in ignored_faces]
    run_id = uuid7(st.clock)
    groups: list[set[str]] = []
    noise = 0
    algo = "hdbscan"
    if len(ids) > LARGE_POOL:
        algo = "knn_graph+chinese_whispers"
        x = np.array([vecs[i] for i in ids], dtype=np.float32)
        edges: dict[tuple[str, str], float] = {}
        for start in range(0, len(ids), 1024):  # k-NN graph in blocks (k=32): memory stays bounded
            sims = x[start : start + 1024] @ x.T
            for r, row in enumerate(sims):
                a = ids[start + r]
                for j in np.argsort(-row)[1:33]:
                    edges[(a, ids[int(j)])] = float(row[j])
        found = F.chinese_whispers(ids, edges, seed=0)
        groups = [g for g in found if len(g) >= F.MIN_GROUP]
        noise = sum(len(g) for g in found if len(g) < F.MIN_GROUP)
    elif len(ids) >= F.MIN_GROUP:
        x = np.array([vecs[i] for i in ids], dtype=np.float32)
        labels = HDBSCAN(min_cluster_size=F.MIN_GROUP, min_samples=2, metric="euclidean").fit_predict(x)
        by: dict[int, set[str]] = {}
        for i, lab in zip(ids, labels, strict=True):
            if lab < 0:
                noise += 1
            else:
                by.setdefault(int(lab), set()).add(i)
        groups = [by[k] for k in sorted(by)]
    # cannot-link: a face rejected for a person never joins that person's group again
    with st.engine.begin() as c:
        rejected = {
            (r[0], r[1])
            for r in c.execute(
                select(face_assignment.c.face_id, face_assignment.c.person_id).where(
                    face_assignment.c.kind == "rejected"
                )
            ).all()
        }
        new_ids = F.stable_ids(soft, groups, new_id=lambda: uuid7(st.clock))
        for pid in set(soft) - set(new_ids):  # soft groups that dissolved
            c.execute(update(face).where(face.c.person_id == pid).values(person_id=None, state="unassigned"))
            c.execute(delete(person).where(person.c.id == pid))
        for pid, g in zip(new_ids, groups, strict=True):
            if pid not in soft:
                c.execute(
                    insert(person).values(
                        id=pid,
                        workspace_id=ws,
                        origin="cluster",
                        status="unnamed",
                        created_by="system",
                        created_at=st.clock.now(),
                    )
                )
            for fid in g:
                if (fid, pid) in rejected:
                    continue
                cur = c.execute(select(face.c.person_id).where(face.c.id == fid)).scalar()
                if cur != pid:
                    _write(c, st, fid, pid, "auto", "cluster", "system", space_id=space)
        c.execute(
            insert(face_cluster_run).values(
                id=run_id,
                workspace_id=ws,
                space_id=space,
                algo=algo,
                params={"min_cluster_size": F.MIN_GROUP, "min_samples": 2, "large_pool": LARGE_POOL},
                input_faces=len(ids),
                clusters=len(groups),
                noise=noise,
                started_at=st.clock.now(),
                finished_at=st.clock.now(),
            )
        )
        for (aid,) in c.execute(select(face.c.asset_id).where(face.c.workspace_id == ws).distinct()).all():
            c.execute(insert(index_outbox).values(asset_id=aid))
    _merge_suggestions(st, ws, space)
    return {"run_id": run_id, "algo": algo, "clusters": len(groups), "noise": noise, "input_faces": len(ids)}


def _merge_suggestions(st: AppState, ws: str, space: int) -> None:
    with st.engine.begin() as c:
        members: dict[str, list[str]] = {}
        for fid, pid in c.execute(
            select(face.c.id, face.c.person_id).where(face.c.workspace_id == ws, face.c.person_id.is_not(None))
        ):
            members.setdefault(pid, []).append(fid)
    vecs = _vectors(st, ws, space, [f for fs in members.values() for f in fs])
    protos = {}
    for pid, fs in members.items():
        vs = [vecs[f] for f in fs if f in vecs]
        if vs:
            protos[pid] = [[sum(col) / len(vs) for col in zip(*vs, strict=True)]]
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    with st.engine.begin() as c:
        for a, b, sc in F.merge_suggestions(protos):
            c.execute(
                pg_insert(person_link)
                .values(workspace_id=ws, person_a=a, person_b=b, kind="merge_suggestion", score=sc)
                .on_conflict_do_update(
                    index_elements=["workspace_id", "person_a", "person_b", "kind"], set_={"score": sc}
                )
            )


# ---------------------------------------------------------------- people
def _people(c: Any, ws: str) -> list[dict[str, Any]]:
    rows = c.execute(
        select(
            person.c.id,
            person.c.display_name,
            person.c.status,
            person.c.is_frozen,
            person.c.age_variation,
            func.count(face.c.id).label("faces"),
            func.count(func.distinct(face.c.asset_id)).label("assets"),
        )
        .join(face, face.c.person_id == person.c.id)
        .where(person.c.workspace_id == ws, person.c.merged_into.is_(None), person.c.status != "ignored")
        .group_by(person.c.id)
        .order_by(func.count(face.c.id).desc(), person.c.id)
    ).all()
    covers = {
        r.person_id: {"asset_id": r.asset_id, "ch": r.content_hash, "bbox": list(r.bbox)}
        for r in c.execute(
            select(face.c.person_id, face.c.asset_id, asset.c.content_hash, face.c.bbox)
            .join(asset, asset.c.id == face.c.asset_id)
            .where(face.c.person_id.in_([r.id for r in rows]))
            .distinct(face.c.person_id)
            .order_by(face.c.person_id, face.c.quality_tier.desc(), face.c.det_score.desc(), face.c.id)
        )
    }
    return [
        {
            "id": r.id,
            "cover": covers.get(r.id),
            "name": r.display_name,
            "status": r.status,
            "frozen": r.is_frozen,
            "age_variation": r.age_variation,
            "face_count": int(r.faces),
            "asset_count": int(r.assets),
        }
        for r in rows
    ]


@router.get("/people")
def people(req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        rows = _people(c, DEFAULT_WS)
        sug = c.execute(
            select(person_link).where(person_link.c.kind == "merge_suggestion", person_link.c.status == "open")
        ).all()
    shown, others = F.fold_small({r["id"]: r["face_count"] for r in rows})
    return {
        "people": [r for r in rows if r["id"] in shown],
        "others": sum(r["face_count"] for r in rows if r["id"] in others),
        "merge_suggestions": [{"a": s.person_a, "b": s.person_b, "score": s.score} for s in sug],
    }


@router.get("/people/{pid}")
def person_detail(pid: str, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        p = next((r for r in _people(c, DEFAULT_WS) if r["id"] == pid), None)
        if p is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        fs = c.execute(
            select(face.c.id, face.c.asset_id, face.c.state, face.c.quality_tier)
            .where(face.c.person_id == pid)
            .order_by(face.c.id)
        ).all()
    return {**p, "faces": [{"id": f.id, "asset_id": f.asset_id, "state": f.state, "tier": f.quality_tier} for f in fs]}


def _freeze(c: Any, st: AppState, pid: str) -> None:
    c.execute(update(person).where(person.c.id == pid).values(is_frozen=True, updated_at=st.clock.now()))


def _reindex(c: Any, pid_or_faces: Any) -> None:
    q = select(face.c.asset_id).distinct()
    q = (
        q.where(face.c.person_id == pid_or_faces)
        if isinstance(pid_or_faces, str)
        else q.where(face.c.id.in_(list(pid_or_faces) or ["-"]))
    )
    for (aid,) in c.execute(q).all():
        c.execute(insert(index_outbox).values(asset_id=aid))


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.post("/people/{pid}/name")
def name_person(pid: str, body: NameIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        clash = c.execute(
            select(person.c.id).where(
                func.lower(person.c.display_name) == body.name.lower(),
                person.c.id != pid,
                person.c.merged_into.is_(None),
            )
        ).scalar()
        if clash:
            raise HTTPException(409, detail={"code": "PERSON_NAME_TAKEN", "person_id": clash})
        c.execute(update(person).where(person.c.id == pid).values(display_name=body.name.strip(), status="named"))
        _freeze(c, st, pid)
        _reindex(c, pid)
    return {"id": pid, "name": body.name.strip(), "folder": F.folder_name(body.name.strip())}


class PersonIn(BaseModel):
    person_id: str


@router.post("/faces/{fid}/confirm")
def confirm(fid: str, body: PersonIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        _write(c, st, fid, body.person_id, "confirmed", "review", "user:local")
        _freeze(c, st, body.person_id)
        _reindex(c, [fid])
    return {"ok": True}


def _reject(c: Any, st: AppState, fid: str, pid: str, method: str = "review") -> None:
    cur = _current(c, [fid]).get(fid)  # auto, confirmed or a pending suggestion for this person
    if cur is not None and cur.person_id == pid:
        _write(c, st, fid, None, "unassigned", method, "user:local")
    c.execute(
        insert(face_assignment).values(
            id=uuid7(st.clock),
            face_id=fid,
            person_id=pid,
            kind="rejected",
            method=method,
            actor="user:local",
            created_at=st.clock.now(),
        )
    )


@router.post("/faces/{fid}/reject")
def reject(fid: str, body: PersonIn, req: Request) -> dict[str, Any]:
    """「不是此人」: a cannot-link that online assignment and clustering always respect (FACE-ASN-006)."""
    st = _st(req)
    with st.engine.begin() as c:
        _reject(c, st, fid, body.person_id)
        _freeze(c, st, body.person_id)
        _reindex(c, [fid])
    return {"ok": True}


CANDIDATE_REFS = 24  # the newest confirmed faces; one k-NN query each
CANDIDATE_K = 200


@router.get("/people/{pid}/candidates")
def person_candidates(pid: str, req: Request, limit: int = Query(60, ge=1, le=200)) -> dict[str, Any]:
    """「可能也是他」: faces like the ones confirmed for this person (before any: the faces of their group), best
    first. Only confirmed faces widen the search, so look-alike children do not pull it astray (FACE-FIND-003)."""
    st = _st(req)
    with st.engine.begin() as c:
        space = fsettings(c).get("space_id")
        who = c.execute(select(person.c.status, person.c.merged_into).where(person.c.id == pid)).first()
        if space is None or who is None or who.status == "ignored" or who.merged_into is not None:
            return {"items": [], "references": 0}
        mine = c.execute(
            select(face_assignment.c.face_id, face_assignment.c.kind).where(
                face_assignment.c.person_id == pid, face_assignment.c.superseded_at.is_(None)
            )
        ).all()
        rejected = {f for f, k in mine if k == "rejected"}
        refs = F.reference_faces([(f, k) for f, k in mine]) or [
            r[0] for r in c.execute(select(face.c.id).where(face.c.person_id == pid).order_by(face.c.id))
        ]
    vecs = _vectors(st, DEFAULT_WS, space, refs[-CANDIDATE_REFS:])
    best: dict[str, float] = {}
    for v in vecs.values():
        for n in _knn(st, DEFAULT_WS, space, v, CANDIDATE_K):
            best[n.face_id] = max(best.get(n.face_id, -1.0), float(n.s))
    with st.engine.begin() as c:
        rows = c.execute(
            select(face.c.id, face.c.asset_id, face.c.person_id, face.c.state, face.c.bbox, asset.c.content_hash)
            .join(asset, asset.c.id == face.c.asset_id)
            .outerjoin(person, person.c.id == face.c.person_id)
            .where(
                face.c.workspace_id == DEFAULT_WS,
                face.c.id.in_(list(best) or ["-"]) | (face.c.person_id == pid),
                person.c.status.is_distinct_from("ignored"),  # someone set aside never comes back this way
            )
        ).all()
    facts = {r.id: (r.asset_id, r.person_id, r.state) for r in rows}
    meta = {r.id: r for r in rows}
    ranked = F.person_candidates(pid, best, facts, rejected)[:limit]
    return {
        "references": len(vecs),
        "items": [
            {
                "face_id": fid,
                "asset_id": meta[fid].asset_id,
                "ch": meta[fid].content_hash,
                "bbox": list(meta[fid].bbox),
                "score": round(s, 4),
                "group": meta[fid].person_id,
            }
            for fid, s in ranked
        ],
    }


class ReviewIn(BaseModel):
    yes: list[str] = Field(default_factory=list, max_length=500)
    no: list[str] = Field(default_factory=list, max_length=500)


@router.post("/people/{pid}/review")
def person_review(pid: str, body: ReviewIn, req: Request) -> dict[str, Any]:
    """Several 「是他」 and 「不是」 at once, from the candidates above."""
    st = _st(req)
    with st.engine.begin() as c:
        ignored = c.execute(
            select(face.c.id)
            .join(person, person.c.id == face.c.person_id)
            .where(face.c.id.in_([*body.yes, *body.no] or ["-"]), person.c.status == "ignored")
        ).first()
        if ignored is not None or c.execute(select(person.c.status).where(person.c.id == pid)).scalar() == "ignored":
            raise HTTPException(409, detail={"code": "FACE_IGNORED"})
        for fid in body.yes:
            _write(c, st, fid, pid, "confirmed", "find", "user:local")
        for fid in body.no:
            _reject(c, st, fid, pid, "find")
        _freeze(c, st, pid)
        _reindex(c, [*body.yes, *body.no])
    return {"confirmed": len(body.yes), "rejected": len(body.no)}


class MergeIn(BaseModel):
    into: str


@router.post("/people/{pid}/merge")
def merge(pid: str, body: MergeIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    token = "mg_" + secrets.token_hex(8)
    with st.engine.begin() as c:
        fs = c.execute(select(face.c.id, face.c.state).where(face.c.person_id == pid)).all()
        for f in fs:
            _write(
                c,
                st,
                f.id,
                body.into,
                "confirmed" if f.state == "confirmed" else "auto",
                "merge",
                "user:local",
                undo_of=token,
            )
        c.execute(update(person).where(person.c.id == pid).values(merged_into=body.into))
        _freeze(c, st, body.into)
        c.execute(
            insert(audit_log).values(
                actor="user:local",
                action="person.merge",
                target=pid,
                details={"into": body.into, "token": token, "faces": len(fs)},
            )
        )
        _reindex(c, body.into)
    return {"undo_token": token, "moved": len(fs)}


@router.post("/people/merges/{token}/undo")
def undo_merge(token: str, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        rows = c.execute(select(face_assignment).where(face_assignment.c.undo_of == token)).all()
        if not rows:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        a = c.execute(
            select(audit_log.c.target).where(
                audit_log.c.action == "person.merge", audit_log.c.details["token"].astext == token
            )
        ).scalar()
        for r in rows:
            prev = (
                c.execute(select(face_assignment).where(face_assignment.c.id == r.replaces)).first()
                if r.replaces
                else None
            )
            c.execute(delete(face_assignment).where(face_assignment.c.id == r.id))
            if prev is not None:
                c.execute(update(face_assignment).where(face_assignment.c.id == prev.id).values(superseded_at=None))
                state = prev.kind if prev.kind in ("auto", "confirmed", "suggested") else "unassigned"
                c.execute(
                    update(face)
                    .where(face.c.id == r.face_id)
                    .values(
                        person_id=prev.person_id if state in ("auto", "confirmed") else None,
                        state=state,
                        confidence=prev.confidence,
                    )
                )
        c.execute(update(person).where(person.c.id == a).values(merged_into=None))
        _reindex(c, [r.face_id for r in rows])
    return {"restored": len(rows)}


class SplitIn(BaseModel):
    face_ids: list[str] = Field(min_length=1)


@router.post("/people/{pid}/split")
def split(pid: str, body: SplitIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    new = uuid7(st.clock)
    with st.engine.begin() as c:
        c.execute(
            insert(person).values(
                id=new,
                workspace_id=DEFAULT_WS,
                origin="split",
                status="unnamed",
                is_frozen=True,
                created_by="user:local",
                created_at=st.clock.now(),
            )
        )
        for fid in body.face_ids:
            _write(c, st, fid, new, "confirmed", "split", "user:local")
        _freeze(c, st, pid)
        _reindex(c, body.face_ids)
    return {"person_id": new}


class IgnoreIn(BaseModel):
    delete_biometrics: bool = False


@router.post("/people/{pid}/ignore")
def ignore(pid: str, body: IgnoreIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        c.execute(update(person).where(person.c.id == pid).values(status="ignored", is_frozen=True))
        fids = [r[0] for r in c.execute(select(face.c.id).where(face.c.person_id == pid)).all()]
        _reindex(c, fids)
    if body.delete_biometrics:  # the person may come back from new photos later (07 §2.4)
        with bio_session(st) as c:
            c.execute(text("DELETE FROM bio.face_embedding WHERE face_id = ANY(:f)"), {"f": fids or ["-"]})
    return {"ignored": len(fids), "biometrics_deleted": body.delete_biometrics}


class FlagsIn(BaseModel):
    age_variation: bool


@router.post("/people/{pid}/flags")
def flags(pid: str, body: FlagsIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        c.execute(update(person).where(person.c.id == pid).values(age_variation=body.age_variation))
        _freeze(c, st, pid)
    return {"ok": True}


@router.get("/faces/review")
def review_queue(req: Request) -> list[dict[str, Any]]:
    """「這是不是 X？」: suggestions, least certain first (07 §2.7)."""
    with _st(req).engine.begin() as c:
        rows = c.execute(
            select(
                face_assignment.c.face_id,
                face_assignment.c.person_id,
                face_assignment.c.confidence,
                person.c.display_name,
                face.c.asset_id,
            )
            .join(person, person.c.id == face_assignment.c.person_id)
            .join(face, face.c.id == face_assignment.c.face_id)
            .where(face_assignment.c.kind == "suggested", face_assignment.c.superseded_at.is_(None))
            .order_by(face_assignment.c.confidence.asc().nulls_first(), face_assignment.c.face_id)
        ).all()
    return [
        {
            "face_id": r.face_id,
            "person_id": r.person_id,
            "person": r.display_name,
            "score": r.confidence,
            "asset_id": r.asset_id,
        }
        for r in rows
    ]


# ---------------------------------------------------------------- find this person (07 §2.5)
class FindIn(BaseModel):
    face_ids: list[str] | None = None
    person_id: str | None = None
    query: str | None = None
    index: int = 0
    workspace: str = DEFAULT_WS


@router.post("/faces/find")
def find(body: FindIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    ws = body.workspace
    with st.engine.begin() as c:
        space = fsettings(c, ws).get("space_id")
        if space is None:
            return {"sure": [], "maybe": []}
        refs: list[str] = list(body.face_ids or [])
        if body.person_id:
            pairs = c.execute(
                select(face_assignment.c.face_id, face_assignment.c.kind).where(
                    face_assignment.c.person_id == body.person_id, face_assignment.c.superseded_at.is_(None)
                )
            ).all()
            refs += F.reference_faces([(a, k) for a, k in pairs]) or [
                r[0] for r in c.execute(select(face.c.id).where(face.c.person_id == body.person_id)).all()
            ]
        refs = [
            r
            for (r,) in c.execute(
                select(face.c.id).where(face.c.id.in_(refs or ["-"]), face.c.workspace_id == ws)
            ).all()
        ]
    qvecs = list(_vectors(st, ws, space, refs).values()) if refs else []
    if body.query:
        h = st.face_queries.get(body.query)
        if h is None or st.clock.now() > h["expires"]:
            st.face_queries.pop(body.query, None)
            raise HTTPException(410, detail={"code": "QUERY_EXPIRED"})
        qvecs.append(h["faces"][body.index]["vec"])
    best: dict[str, tuple[str, float]] = {}
    for q in qvecs:
        for n in _knn(st, ws, space, q, 500):
            if n.face_id not in best or n.s > best[n.face_id][1]:
                best[n.face_id] = ("", float(n.s))
    with st.engine.begin() as c:
        owner = dict(
            c.execute(
                select(face.c.id, face.c.asset_id).where(face.c.id.in_(list(best) or ["-"]), face.c.workspace_id == ws)
            ).all()
        )
    tiers = F.find_tiers({f: (owner[f], s) for f, (_x, s) in best.items() if f in owner})
    return {k: [{"asset_id": a, "score": round(s, 4)} for a, s in v] for k, v in tiers.items()}


def embed_query_via_worker(st: AppState, data: bytes) -> list[dict[str, Any]]:
    """``face_embed_query`` on the interactive lane: the worker fetches the bytes once by token (like by-image)."""
    import time
    import uuid

    token = secrets.token_urlsafe(16)
    st.blobs[token] = data
    tid = "rpc-" + uuid.uuid4().hex[:12]
    try:
        with st.engine.begin() as c:
            w = c.execute(text("SELECT id FROM worker ORDER BY last_heartbeat_at DESC NULLS LAST LIMIT 1")).scalar()
            c.execute(
                insert(worker_task).values(
                    id=tid, kind="face_embed_query", lane="interactive", worker_id=w, payload={"blob": token}
                )
            )
        deadline = time.monotonic() + 10  # noqa: TID251 - waits on a real worker over the wire
        while time.monotonic() < deadline:  # noqa: TID251
            with st.engine.begin() as c:
                row = c.execute(
                    select(worker_task.c.status, worker_task.c.result).where(worker_task.c.id == tid)
                ).first()
            if row is not None and row.status in ("done", "failed"):
                return list((row.result or {}).get("faces") or [])
            time.sleep(0.05)  # noqa: TID251
        raise HTTPException(503, detail={"code": "WORKER_OFFLINE"})
    finally:
        st.blobs.pop(token, None)
        with st.engine.begin() as c:  # the result held vectors: never keep it in the queue table
            c.execute(update(worker_task).where(worker_task.c.id == tid).values(result=None))


@router.post("/faces/query-image")
async def query_image(req: Request) -> dict[str, Any]:
    """Drag a photo to find a person: vectors only in memory, behind a 10-minute handle (FACE-FIND-002)."""
    from starlette.concurrency import run_in_threadpool

    st = _st(req)
    form = await req.form()
    up = form.get("file")
    if up is None or isinstance(up, str):
        raise HTTPException(400, detail={"code": "FILE_REQUIRED"})
    data = await up.read()
    faces = await run_in_threadpool(embed_query_via_worker, st, data)
    del data
    handle = "fq_" + secrets.token_urlsafe(12)
    usable = [f for f in faces if f.get("vec")]
    st.face_queries[handle] = {"faces": usable, "expires": st.clock.now() + timedelta(seconds=QUERY_TTL_S)}
    return {
        "handle": handle,
        "faces": [{"index": i, "bbox": f["bbox"], "quality_tier": f["quality_tier"]} for i, f in enumerate(usable)],
    }


# ---------------------------------------------------------------- {person} output (FACE-TPL-001)
class ByPersonIn(BaseModel):
    target: dict[str, str]
    path_template: str
    fallback: str = "_未知人物"


@router.post("/faces/plan-by-person")
def plan_by_person(body: ByPersonIn, req: Request) -> dict[str, Any]:
    """A draft copy plan with ``{person}``: one copy per named person in the photo (fan-out), the fallback when
    nobody is named. Approved like any plan (invariant 3)."""
    from opagent_core.planner import PlanItem, assign_collisions, plan, summarize
    from opagent_db.schema import job, output_action, output_plan

    st = _st(req)
    with st.engine.begin() as c:
        rows = c.execute(
            select(
                asset_location.c.asset_id,
                asset_location.c.rel_path,
                asset_location.c.mtime,
                source.c.root_alias,
                source.c.worker_id,
                asset.c.content_hash,
                asset.c.taken_at,
                asset.c.date_source,
            )
            .join(source, source.c.id == asset_location.c.source_id)
            .join(asset, asset.c.id == asset_location.c.asset_id)
            .where(asset_location.c.missing.is_(False))
            .order_by(asset_location.c.asset_id, asset_location.c.rel_path)
        ).all()
        first: dict[str, Any] = {}
        for r in rows:
            first.setdefault(r.asset_id, r)
        names: dict[str, list[str]] = {}
        for aid, nm in c.execute(
            select(face.c.asset_id, person.c.display_name)
            .join(person, person.c.id == face.c.person_id)
            .where(person.c.display_name.is_not(None), person.c.status == "named", person.c.merged_into.is_(None))
            .distinct()
        ).all():
            names.setdefault(aid, []).append(nm)
        acts = []
        for aid, r in first.items():
            for nm in sorted(names.get(aid) or [body.fallback]):
                tpl = body.path_template.replace("{person}", F.folder_name(nm))
                acts += [
                    (r, a)
                    for a in plan(
                        [
                            PlanItem(
                                asset_id=aid,
                                src_rel=r.rel_path,
                                content_hash=r.content_hash,
                                taken_at=r.taken_at,
                                taken_at_source=r.date_source,
                                mtime=r.mtime,
                            )
                        ],
                        "by-date",
                        body.target.get("sub_path", "").strip("/"),
                        template=tpl,
                    )
                ]
        planned = assign_collisions([a for _, a in acts])
        src = {a.asset_id: r for r, a in acts}
        jid, pid = uuid7(st.clock), uuid7(st.clock)
        c.execute(
            insert(job).values(
                id=jid,
                workspace_id=DEFAULT_WS,
                kind="organize",
                status="awaiting_approval",
                mode="dry_run",
                plan_id=pid,
                params={
                    "preset": "person",
                    "target": body.target,
                    "worker_id": next(iter(first.values())).worker_id if first else None,
                },
            )
        )
        c.execute(insert(output_plan).values(id=pid, job_id=jid, status="draft", summary=summarize(planned)))
        if planned:
            c.execute(
                insert(output_action),
                [
                    {
                        "id": uuid7(st.clock),
                        "plan_id": pid,
                        "seq": i,
                        "asset_id": a.asset_id,
                        "action": "copy",
                        "src_alias": src[a.asset_id].root_alias,
                        "src_rel": a.src_rel,
                        "dst_alias": body.target["root_alias"],
                        "dst_rel": a.dst_rel,
                        "src_hash": a.src_hash,
                        "date_source": a.date_source,
                        "conflict": a.conflict,
                    }
                    for i, a in enumerate(planned)
                ],
            )
    return {"plan_id": pid, "job_id": jid, "actions": len(planned)}


# ---------------------------------------------------------------- purge, maintenance, model switch
class PurgeIn(BaseModel):
    confirm: str


@router.post("/faces/purge")
def purge(body: PurgeIn, req: Request) -> dict[str, Any]:
    """Delete every face datum of the workspace in one transaction (07 §6.3; FACE-PRG-001). The Worker then
    removes face thumbnails and destroys the workspace key (crypto-shredding), first thing when it is online."""
    ui_only(req)
    st = _st(req)
    with st.engine.begin() as c:
        name = c.execute(select(workspace.c.name).where(workspace.c.id == DEFAULT_WS)).scalar()
        if body.confirm != name:
            raise HTTPException(422, detail={"code": "CONFIRM_TEXT_MISMATCH", "expected": name})
    return purge_workspace(st, DEFAULT_WS)


def purge_workspace(st: AppState, ws: str) -> dict[str, Any]:
    p = _part(ws)
    with bio_session(st) as c:
        emb = c.execute(text("SELECT count(*) FROM bio.face_embedding WHERE workspace_id = :w"), {"w": ws}).scalar()
        protos = c.execute(
            text("SELECT count(*) FROM bio.person_prototype WHERE workspace_id = :w"), {"w": ws}
        ).scalar()
        c.execute(text(f"DROP TABLE IF EXISTS bio.face_embedding_{p}"))
        c.execute(text(f"DROP TABLE IF EXISTS bio.person_prototype_{p}"))
        n_faces = c.execute(select(func.count()).select_from(face).where(face.c.workspace_id == ws)).scalar_one()
        n_persons = c.execute(select(func.count()).select_from(person).where(person.c.workspace_id == ws)).scalar_one()
        assets = [r[0] for r in c.execute(select(face.c.asset_id).where(face.c.workspace_id == ws).distinct()).all()]
        c.execute(
            delete(face_assignment).where(
                face_assignment.c.face_id.in_(select(face.c.id).where(face.c.workspace_id == ws))
            )
        )
        c.execute(delete(face).where(face.c.workspace_id == ws))
        c.execute(delete(person_link).where(person_link.c.workspace_id == ws))
        c.execute(update(person).where(person.c.workspace_id == ws).values(merged_into=None))
        c.execute(delete(person).where(person.c.workspace_id == ws))
        c.execute(delete(face_cluster_run).where(face_cluster_run.c.workspace_id == ws))
        c.execute(
            update(search_doc)
            .where(search_doc.c.workspace_id == ws)
            .values(person_ids=None, person_ids_suggested=None, face_count=None)
        )
        for aid in assets:
            c.execute(insert(index_outbox).values(asset_id=aid))
        cur = c.execute(select(workspace.c.settings).where(workspace.c.id == ws)).scalar() or {}
        c.execute(
            update(workspace)
            .where(workspace.c.id == ws)
            .values(settings={**cur, "faces": {"enabled": False, "prompt_dismissed": True}})
        )
        c.execute(
            insert(face_consent).values(
                id=uuid7(st.clock),
                workspace_id=ws,
                user_id="local",
                action="purge",
                consent_text_version="-",
                at=st.clock.now(),
            )
        )
        report: dict[str, Any] = {
            "id": uuid7(st.clock),
            "workspace": ws,
            "at": st.clock.now().isoformat(),
            "faces": int(n_faces),
            "embeddings": int(emb or 0),
            "prototypes": int(protos or 0),
            "persons": int(n_persons),
            "locations": ["database:bio", "database:face", "worker:faces"],
            "worker_tasks": [],
        }
        workers = [r[0] for r in c.execute(text("SELECT id FROM worker")).all()]
        report["worker_tasks"] = [uuid7(st.clock) for _ in workers]
        report["sha256"] = report_hash(report)  # SEC-FACE-001: the report itself is tamper-evident
        c.execute(
            insert(audit_log).values(actor="user:local", action="faces.purge", target=ws, outcome="ok", details=report)
        )
        for wid, tid in zip(workers, report["worker_tasks"], strict=True):  # pending until the worker is back (PRG-003)
            c.execute(
                insert(worker_task).values(
                    id=tid, kind="face_purge", worker_id=wid, lane="control", payload={"workspace_id": ws}
                )
            )
    return {**report, "worker_pending": True}


def report_hash(report: dict[str, Any]) -> str:
    import hashlib
    import json

    body = {k: v for k, v in report.items() if k != "sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class VerifyIn(BaseModel):
    report_id: str


@router.post("/faces/purge/verify")
def verify_purge(body: VerifyIn, req: Request) -> dict[str, Any]:
    """Re-check a deletion report (09b SEC-FACE-001): its hash, that no face datum from before it exists in the
    database or the ``bio`` schema, and that every worker confirmed destroying its key and face crops."""
    from datetime import datetime

    st = _st(req)
    with st.engine.begin() as c:
        row = c.execute(
            select(audit_log.c.details).where(
                audit_log.c.action == "faces.purge", audit_log.c.details["id"].astext == body.report_id
            )
        ).first()
        if row is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        rep = dict(row.details)
        at = datetime.fromisoformat(rep["at"])
        ws = rep["workspace"]
        left = c.execute(
            select(func.count()).select_from(face).where(face.c.workspace_id == ws, face.c.created_at <= at)
        ).scalar_one()
        left += c.execute(
            select(func.count()).select_from(person).where(person.c.workspace_id == ws, person.c.created_at <= at)
        ).scalar_one()
        tasks = c.execute(
            select(worker_task.c.status, worker_task.c.result).where(
                worker_task.c.id.in_(rep.get("worker_tasks") or ["-"])
            )
        ).all()
    with bio_session(st) as c:
        bio_left = (
            c.execute(
                text("SELECT count(*) FROM bio.face_embedding WHERE workspace_id = :w AND created_at <= :at"),
                {"w": ws, "at": at},
            ).scalar()
            + c.execute(  # prototypes of people that no longer exist
                text(
                    "SELECT count(*) FROM bio.person_prototype pp WHERE pp.workspace_id = :w"
                    " AND NOT EXISTS (SELECT 1 FROM person p WHERE p.id = pp.person_id)"
                ),
                {"w": ws},
            ).scalar()
        )
    worker_ok = len(tasks) == len(rep.get("worker_tasks") or []) > 0 and all(
        t.status == "done" and bool((t.result or {}).get("key_destroyed")) and (t.result or {}).get("vault_removed")
        for t in tasks
    )
    checks = [
        {"id": "report_hash", "ok": rep.get("sha256") == report_hash(rep)},
        {"id": "database", "ok": left == 0, "remaining": int(left)},
        {"id": "bio_schema", "ok": bio_left == 0, "remaining": int(bio_left or 0)},
        {"id": "worker", "ok": worker_ok, "workers": len(tasks)},
    ]
    return {"report": rep, "verified": all(c["ok"] for c in checks), "checks": checks}


@router.post("/faces/maintenance")
def maintenance(req: Request) -> dict[str, Any]:
    """Data minimisation (07 §6.2 F8): faces of photos gone 30 days; face spaces retired 7 days ago."""
    st = _st(req)
    now = st.clock.now()
    with st.engine.begin() as c:
        alive = select(asset_location.c.asset_id).where(
            (asset_location.c.missing.is_(False)) | (asset_location.c.last_seen_at > now - VANISHED_AFTER)
        )
        gone = [r[0] for r in c.execute(select(face.c.id).where(face.c.asset_id.notin_(alive))).all()]
        c.execute(delete(face).where(face.c.id.in_(gone or ["-"])))
        old = [
            r[0]
            for r in c.execute(
                select(embedding_space.c.id).where(
                    embedding_space.c.modality == "face",
                    embedding_space.c.status == "retired",
                    embedding_space.c.created_at < now - SPACE_RETAIN,
                )
            ).all()
        ]
    with bio_session(st) as c:
        c.execute(text("DELETE FROM bio.face_embedding WHERE face_id = ANY(:f)"), {"f": gone or ["-"]})
        c.execute(text("DELETE FROM bio.face_embedding WHERE space_id = ANY(:s)"), {"s": old or [-1]})
    return {"deleted_faces": len(gone), "deleted_spaces": len(old)}


class MigrationIn(BaseModel):
    fingerprint: str


@router.post("/faces/space-migrations")
def start_migration(body: MigrationIn, req: Request) -> dict[str, Any]:
    """Blue-green model switch (07 §2.8): a new face space fills while the old one keeps answering."""
    st = _st(req)
    with st.engine.begin() as c:
        sid = _space(c, body.fingerprint)
        c.execute(update(embedding_space).where(embedding_space.c.id == sid).values(status="building"))
    return {"id": str(sid)}


class ReembedIn(BaseModel):
    migration_id: str
    face_id: str
    vec: list[float]


@worker_router.post("/faces/reembed")
def reembed(body: ReembedIn, req: Request) -> dict[str, Any]:
    if not body.migration_id.isdigit():
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    with bio_session(_st(req)) as c:
        c.execute(
            text(
                "INSERT INTO bio.face_embedding(workspace_id, face_id, space_id, vec) VALUES (:w,:f,:s,:v) "
                "ON CONFLICT DO NOTHING"
            ),
            {"w": DEFAULT_WS, "f": body.face_id, "s": int(body.migration_id), "v": str(body.vec)},
        )
    return {"ok": True}


@router.post("/faces/space-migrations/{mid}/complete")
def complete_migration(mid: str, req: Request) -> dict[str, Any]:
    """Human decisions carry over unchanged (they reference faces, not vectors); then switch atomically and
    retire the old space, deleted after 7 days by maintenance."""
    st = _st(req)
    if not mid.isdigit():
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    new = int(mid)
    with st.engine.begin() as c:
        s = fsettings(c)
        old = s.get("space_id")
        c.execute(update(embedding_space).where(embedding_space.c.id == new).values(status="active"))
        if old:
            c.execute(
                update(embedding_space)
                .where(embedding_space.c.id == old)
                .values(status="retired", created_at=st.clock.now())
            )
        c.execute(update(face).where(face.c.workspace_id == DEFAULT_WS).values(space_id=new))
        _put(c, DEFAULT_WS, {**s, "space_id": new})
    return {"space_id": new, "retired": old}


# ---------------------------------------------------------------- search_doc fields (07 §3.3)
def person_fields(c: Any, asset_id: str) -> dict[str, Any]:
    if not fsettings(c).get("enabled"):
        return {"person_ids": None, "person_ids_suggested": None, "face_count": None}
    rows = c.execute(
        select(face.c.person_id, face.c.state, face.c.quality_tier, face_assignment.c.person_id.label("sug"))
        .outerjoin(
            face_assignment,
            and_(
                face_assignment.c.face_id == face.c.id,
                face_assignment.c.superseded_at.is_(None),
                face_assignment.c.kind == "suggested",
            ),
        )
        .where(face.c.asset_id == asset_id)
    ).all()
    live = {
        r[0]
        for r in c.execute(
            select(person.c.id).where(person.c.status != "ignored", person.c.merged_into.is_(None))
        ).all()
    }
    return {
        "person_ids": sorted({r.person_id for r in rows if r.state in ("auto", "confirmed") and r.person_id in live}),
        "person_ids_suggested": sorted({r.sug for r in rows if r.sug and r.sug in live}),
        "face_count": sum(1 for r in rows if r.quality_tier >= 1),
    }
