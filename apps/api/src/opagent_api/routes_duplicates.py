"""Duplicate groups (05 §2.2; 09b SRCH-DUP-*). Exact: one asset (content hash) at several locations. Near: pHash /
embedding neighbours grouped by `recluster` (union-find). Rows only — the API never reads photo files."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from opagent_core.ids import uuid7
from opagent_db.schema import asset, asset_embedding, asset_location, duplicate_member
from opagent_search.dedup import group_near
from opagent_search.fusion import representative
from sqlalchemy import delete, func, insert, select

from .operations import ensure_operation, set_op_status
from .search import active_space
from .state import DEFAULT_WS, AppState

router = APIRouter()
BRUTE_VECTORS = 2000  # above this, embedding neighbours come from pgvector k-NN (k = 10) instead of all pairs


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
                    "members": [{"asset_id": a.id, "source_id": s, "rel_path": r} for s, r in locs],
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


@router.post("/duplicates/recluster")
def recluster(req: Request) -> dict[str, Any]:
    """The `dedup` background task (05 §2.2) as an Operation: recompute near-duplicate groups from stored hashes and
    vectors; previous groups are replaced atomically."""
    st = _st(req)
    op_id = uuid7(st.clock)
    with st.engine.begin() as c:
        ensure_operation(c, st.clock, op_id, "dedup")
        set_op_status(c, st.clock, op_id, "running")
        assets = c.execute(select(asset.c.id, asset.c.phash).where(asset.c.workspace_id == DEFAULT_WS)).all()
        space = active_space(c)
        vecs: dict[str, list[float]] = {}
        if space is not None:
            for aid, v in c.execute(
                select(asset_embedding.c.asset_id, asset_embedding.c.vec).where(asset_embedding.c.space_id == space.id)
            ).all():
                vecs[aid] = [float(x) for x in (v if not isinstance(v, str) else v.strip("[]").split(","))]
        items = [(aid, ph, vecs.get(aid)) for aid, ph in assets]
        index = {aid: i for i, (aid, _, _) in enumerate(items)}
        neighbors = None
        if space is not None and len(vecs) > BRUTE_VECTORS:

            def neighbors(i: int) -> list[int]:
                rows = c.execute(
                    select(asset_embedding.c.asset_id)
                    .where(asset_embedding.c.space_id == space.id, asset_embedding.c.asset_id != items[i][0])
                    .order_by(asset_embedding.c.vec.cosine_distance(str(items[i][2])))
                    .limit(10)
                ).scalars()
                return [index[a] for a in rows if a in index]

        groups = group_near(items, neighbors=neighbors)
        c.execute(delete(duplicate_member))
        sizes = dict(c.execute(select(asset.c.id, asset.c.width * asset.c.height)).all())
        for g in groups:
            gid = uuid7(st.clock)
            rep = max(g, key=lambda a: (sizes.get(a) or 0, a))
            c.execute(
                insert(duplicate_member),
                [{"group_id": gid, "asset_id": a, "reason": "near", "is_representative": a == rep} for a in g],
            )
        set_op_status(c, st.clock, op_id, "succeeded")
    return {"operation_id": op_id, "groups": len(groups)}
