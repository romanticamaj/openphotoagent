"""Duplicate groups (05 §2.2; 09b SRCH-DUP-*). Exact: one asset (content hash) at several locations. Near: pHash /
embedding neighbours grouped by `recluster` (union-find). Rows only — the API never reads photo files."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from opagent_core.ids import uuid7
from opagent_db.schema import Vector, asset, asset_embedding, asset_location, duplicate_member
from opagent_search.dedup import group_duplicates
from opagent_search.fusion import representative
from pydantic import BaseModel
from sqlalchemy import bindparam, cast, delete, func, insert, select

from .operations import ensure_operation, set_op_status
from .search import active_space
from .state import DEFAULT_WS, AppState

router = APIRouter()


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def _asset_view(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "asset_id": r.id,
        "width": r.width,
        "height": r.height,
        "size_bytes": r.size_bytes,
        "taken_at": r.taken_at,
        "ch": r.content_hash,
    }


@router.get("/duplicates")
def list_duplicates(req: Request) -> dict[str, Any]:
    st = _st(req)
    groups: list[dict[str, Any]] = []
    with st.engine.begin() as c:
        multi = (
            select(asset_location.c.asset_id)
            .where(asset_location.c.missing.is_(False))
            .group_by(asset_location.c.asset_id)
            .having(func.count() > 1)
        )
        for a in c.execute(select(asset).where(asset.c.workspace_id == DEFAULT_WS, asset.c.id.in_(multi))).all():
            locs = c.execute(
                select(asset_location.c.source_id, asset_location.c.rel_path)
                .where(asset_location.c.asset_id == a.id, asset_location.c.missing.is_(False))
                .order_by(asset_location.c.rel_path)
            ).all()
            groups.append(
                {
                    "group_id": f"hash:{a.content_hash}",
                    "reason": "hash",
                    "members": [
                        {"asset_id": a.id, "source_id": s, "rel_path": r, "ch": a.content_hash} for s, r in locs
                    ],
                    "representative": {"asset_id": a.id},
                    "reclaimable_bytes": (len(locs) - 1) * int(a.size_bytes),
                }
            )
        rows = c.execute(
            select(duplicate_member.c.group_id, asset)
            .join(asset, asset.c.id == duplicate_member.c.asset_id)
            .order_by(duplicate_member.c.group_id, asset.c.id)
        ).all()
        first_loc = dict(
            c.execute(
                select(asset_location.c.asset_id, func.min(asset_location.c.rel_path)).group_by(
                    asset_location.c.asset_id
                )
            ).all()
        )
    by_group: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_group.setdefault(r.group_id, []).append({**_asset_view(r), "rel_path": first_loc.get(r.id)})
    for gid, members in by_group.items():
        rep = representative(members)
        groups.append(
            {
                "group_id": gid,
                "reason": "near",
                "members": members,
                "representative": {"asset_id": rep["asset_id"]},
                "reclaimable_bytes": sum(int(m["size_bytes"]) for m in members if m["asset_id"] != rep["asset_id"]),
            }
        )
    return {"groups": groups}


@router.get("/assets/{asset_id}/duplicates")
def asset_duplicates(asset_id: str, req: Request) -> dict[str, Any]:
    """A photo's near-duplicate group, its kept photo first (SRCH-DUP-011): what 「+N」 on a search result opens. A
    photo in no group is a group of one. Rows only."""
    with _st(req).engine.begin() as c:
        gid = c.execute(select(duplicate_member.c.group_id).where(duplicate_member.c.asset_id == asset_id)).scalar()
        ids = (
            [
                r[0]
                for r in c.execute(
                    select(duplicate_member.c.asset_id)
                    .where(duplicate_member.c.group_id == gid)
                    .order_by(duplicate_member.c.is_representative.desc(), duplicate_member.c.asset_id)
                )
            ]
            if gid
            else [asset_id]
        )
        rows = {
            r.id: r
            for r in c.execute(
                select(asset.c.id, asset.c.content_hash, asset.c.rotation, asset.c.taken_at).where(
                    asset.c.workspace_id == DEFAULT_WS, asset.c.id.in_(ids)
                )
            )
        }
    return {
        "photos": [
            {
                "asset_id": a,
                "ch": rows[a].content_hash,
                "rot": rows[a].rotation,
                "taken_at": rows[a].taken_at.isoformat() if rows[a].taken_at else None,
            }
            for a in ids
            if a in rows
        ]
    }


def regroup(st: Any, c: Any) -> int:
    """Near-duplicate groups from stored hashes and vectors, the previous ones replaced in the same transaction. Run
    when an analysis finishes (SRCH-DUP-005): nothing called the route, the groups stayed empty and search counted
    every copy."""
    # duplicates are pHash near-copies of the photo kept, vetoed by the image vector (ADR-0043); look-alikes found by
    # the vector alone grouped different children in one gym and offered them for cleanup
    rows = c.execute(
        select(asset.c.id, asset.c.phash, asset.c.width * asset.c.height, asset.c.size_bytes).where(
            asset.c.workspace_id == DEFAULT_WS
        )
    ).all()
    space = active_space(c)
    cache: dict[str, list[float] | None] = {}

    def vector(aid: str) -> list[float] | None:
        if aid not in cache:
            v = (
                c.execute(
                    select(asset_embedding.c.vec).where(
                        asset_embedding.c.asset_id == aid, asset_embedding.c.space_id == space.id
                    )
                ).scalar()
                if space is not None
                else None
            )
            cache[aid] = (
                None if v is None else [float(x) for x in (v if not isinstance(v, str) else v.strip("[]").split(","))]
            )
        return cache[aid]

    rank = {r[0]: (r[2] or 0, r[3] or 0) for r in rows}  # highest resolution, then largest file (SRCH-DUP-003)
    groups = group_duplicates([(r[0], r[1], None) for r in rows], rank, vector=vector)
    c.execute(delete(duplicate_member))
    for g in groups:
        gid = uuid7(st.clock)
        rep = max(g, key=lambda a: (rank.get(a, (0, 0)), a))
        c.execute(
            insert(duplicate_member),
            [{"group_id": gid, "asset_id": a, "reason": "near", "is_representative": a == rep} for a in g],
        )
    return len(groups)


@router.post("/duplicates/recluster")
def recluster(req: Request) -> dict[str, Any]:
    """The `dedup` background task (05 §2.2) as an Operation: recompute near-duplicate groups from stored hashes and
    vectors; previous groups are replaced atomically."""
    st = _st(req)
    op_id = uuid7(st.clock)
    with st.engine.begin() as c:
        ensure_operation(c, st.clock, op_id, "dedup")
        set_op_status(c, st.clock, op_id, "running")
        n = regroup(st, c)
        set_op_status(c, st.clock, op_id, "succeeded")
    return {"operation_id": op_id, "groups": n}


@router.get("/assets/{asset_id}/similar")
def similar(
    asset_id: str, req: Request, k: int = 20, min_score: float = 0.0, exclude_duplicates: bool = True
) -> dict[str, Any]:
    """find_similar (05 §2.2): the asset's own vector in the active space → k-NN by cosine; no inference."""
    from fastapi import HTTPException
    from sqlalchemy import and_, literal_column

    st = _st(req)
    k = max(1, min(int(k), 200))
    with st.engine.begin() as c:
        space = active_space(c)
        vec = (
            c.execute(
                select(asset_embedding.c.vec).where(
                    asset_embedding.c.asset_id == asset_id, asset_embedding.c.space_id == space.id
                )
            ).scalar()
            if space is not None
            else None
        )
        if vec is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        qv = vec if isinstance(vec, str) else str([float(x) for x in vec])
        dist = asset_embedding.c.vec.op("<=>")(cast(bindparam("qv", qv), Vector()))
        q = (
            select(asset_embedding.c.asset_id, (literal_column("1") - dist).label("score"))
            .where(and_(asset_embedding.c.space_id == space.id, asset_embedding.c.asset_id != asset_id))
            .order_by(dist, asset_embedding.c.asset_id)
            .limit(k)
        )
        if exclude_duplicates:
            group = select(duplicate_member.c.group_id).where(duplicate_member.c.asset_id == asset_id)
            same = select(duplicate_member.c.asset_id).where(duplicate_member.c.group_id.in_(group))
            q = q.where(asset_embedding.c.asset_id.notin_(same))
        rows = c.execute(q).all()
    return {"items": [{"asset_id": a, "score": round(float(s), 6)} for a, s in rows if float(s) >= min_score]}


class CleanupIn(BaseModel):
    group_ids: list[str]


@router.post("/duplicates/cleanup-plan")
def cleanup_plan(body: CleanupIn, req: Request) -> dict[str, Any]:
    """One-click duplicate clean-up (09b TRASH-005): a draft output plan that keeps each group's representative and
    moves every other copy to its root's `.opagent-trash/` (reversible). Nothing changes until a person approves."""
    from fastapi import HTTPException
    from opagent_db.schema import source, worker

    st = _st(req)
    wanted = set(body.group_ids)
    groups = [g for g in list_duplicates(req)["groups"] if g["group_id"] in wanted]
    if not groups:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    with st.engine.begin() as c:
        srcs = {r.id: r for r in c.execute(select(source)).all()}
        modes = {}
        for wid, roots in c.execute(select(worker.c.id, worker.c.roots)).all():
            for r in roots or []:
                modes[(wid, r["alias"])] = r.get("mode", "ro")
        drops: list[tuple[str, str, str, str]] = []  # (asset_id, source_id, rel_path, hash)
        for g in groups:
            rep = g["representative"]["asset_id"]
            if g["reason"] == "hash":
                drops += [(m["asset_id"], m["source_id"], m["rel_path"], "") for m in g["members"][1:]]
            else:
                for m in g["members"]:
                    if m["asset_id"] == rep:
                        continue
                    for sid, rel in c.execute(
                        select(asset_location.c.source_id, asset_location.c.rel_path).where(
                            asset_location.c.asset_id == m["asset_id"], asset_location.c.missing.is_(False)
                        )
                    ).all():
                        drops.append((m["asset_id"], sid, rel, ""))
        return trash_plan(st, c, srcs, modes, drops, "cleanup", {"groups": sorted(wanted)})


def trash_plan(
    st: Any,
    c: Any,
    srcs: dict[str, Any],
    modes: dict[tuple[str, str], str],
    drops: list[tuple[str, str, str, str]],
    kind: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """A draft plan moving each (asset, source, rel) into its own root's `.opagent-trash/` (TRASH-005, TRASH-011):
    nothing changes until a person approves; undo puts the files back (invariant 3). A read-only root refuses the
    plan with its reason, never another action (TRASH-004)."""
    from fastapi import HTTPException
    from opagent_db.schema import job, output_action, output_plan

    hashes = dict(c.execute(select(asset.c.id, asset.c.content_hash)).all())
    blocked = sorted(
        {
            f"{srcs[sid].root_alias}"
            for _a, sid, _r, _h in drops
            if modes.get((srcs[sid].worker_id, srcs[sid].root_alias), "ro") != "rw"
        }
    )
    if blocked:  # plan-time blocker, never quietly turned into another action (TRASH-004)
        raise HTTPException(409, detail={"code": "FS_NOT_WRITABLE", "roots": blocked})
    wid = next((srcs[sid].worker_id for _a, sid, _r, _h in drops), None)
    jid, pid = uuid7(st.clock), uuid7(st.clock)
    c.execute(
        insert(job).values(
            id=jid,
            workspace_id=DEFAULT_WS,
            kind=kind,
            status="awaiting_approval",
            params={"worker_id": wid, **params},
        )
    )
    c.execute(
        insert(output_plan).values(
            id=pid,
            job_id=jid,
            status="draft",
            summary={"trash": len(drops), "total": len(drops), "folders": 0, "kind": kind},
        )
    )
    if drops:
        c.execute(
            insert(output_action),
            [
                {
                    "id": uuid7(st.clock),
                    "plan_id": pid,
                    "seq": i,
                    "asset_id": aid,
                    "action": "trash",
                    "src_alias": srcs[sid].root_alias,
                    "src_rel": rel,
                    "dst_alias": srcs[sid].root_alias,
                    "dst_rel": rel,
                    "src_hash": hashes[aid],
                }
                for i, (aid, sid, rel, _h) in enumerate(drops)
            ],
        )
    return {"plan_id": pid, "job_id": jid, "trash": len(drops)}
