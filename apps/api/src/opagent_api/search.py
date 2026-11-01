"""Hybrid search (05 §2.1): R1 image vectors (query vector from the Worker's text tower), R3 full text (words, then
CJK-bigram downgrade), R4 tag keys, fused with weighted RRF (k=60). Filters and facets share one permission-scoped
base set (``scope_keys``), so facet counts never leak outside the requested scope. Cursors are HMAC-signed and pin a
snapshot, so paging never repeats or skips when photos are added meanwhile.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from opagent_db.schema import asset_embedding, embedding_space, search_doc, worker, worker_task
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, text

from .state import AppState

router = APIRouter()
RRF_K = 60
W = {"image_vector": 1.0, "fulltext": 1.0, "tag": 1.2}
PER_ROUTE = 200
MIN_WORD_HITS = 5  # fewer word-AND hits → downgrade to bigram AND (05 §1.3 rule 3)
RPC_TIMEOUT_S = 2.0
_SECRET = secrets.token_bytes(32)


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


class Scope(BaseModel):
    source_id: str
    path: str = ""


class Filters(BaseModel):
    tags_all: list[str] | None = None
    category: str | None = None
    taken_from: str | None = None
    taken_to: str | None = None
    date_source: str | None = None
    scope: list[Scope] | None = None


class SearchIn(BaseModel):
    q: str | None = None
    filters: Filters = Field(default_factory=Filters)
    semantic: bool = True
    limit: int = Field(default=50, ge=1, le=500)
    cursor: str | None = None
    facets: list[str] = []


# ---- cursors ----
def _sign(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    mac = hmac.new(_SECRET, raw, hashlib.sha256).digest()[:12]
    return base64.urlsafe_b64encode(raw + mac).decode().rstrip("=")


def _unsign(tok: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4))
        body, mac = raw[:-12], raw[-12:]
        if not hmac.compare_digest(hmac.new(_SECRET, body, hashlib.sha256).digest()[:12], mac):
            raise ValueError("bad mac")
        return dict(json.loads(body))
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(400, detail={"code": "CURSOR_INVALID"}) from None


def _qhash(body: SearchIn) -> str:
    d = body.model_dump(exclude={"cursor", "limit"})
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ---- query vector over the worker RPC ----
def _worker_online(c: Any, st: AppState) -> Any:
    from datetime import UTC, timedelta

    w = c.execute(select(worker).order_by(worker.c.last_heartbeat_at.desc().nulls_last()).limit(1)).first()
    if w is None or w.last_heartbeat_at is None:
        return None
    hb = w.last_heartbeat_at if w.last_heartbeat_at.tzinfo else w.last_heartbeat_at.replace(tzinfo=UTC)
    return w if st.clock.now() - hb <= timedelta(seconds=90) else None


def query_vector(st: AppState, text_q: str, space: Any) -> list[float] | None:
    """``embed.text`` RPC on the interactive lane; ``None`` when no worker answers within 2 s (SRCH-SEM-002)."""
    import uuid

    with st.engine.begin() as c:
        w = _worker_online(c, st)
        if w is None or space is None:
            return None
        tid = "rpc-" + uuid.uuid4().hex[:12]
        c.execute(
            worker_task.insert().values(
                id=tid,
                kind="embed_text",
                lane="interactive",
                worker_id=w.id,
                payload={"text": text_q, "fingerprint": space.fingerprint},
            )
        )
    deadline = time.monotonic() + RPC_TIMEOUT_S  # noqa: TID251 - waits on a real worker over the wire
    while time.monotonic() < deadline:  # noqa: TID251
        with st.engine.begin() as c:
            row = c.execute(select(worker_task.c.status, worker_task.c.result).where(worker_task.c.id == tid)).first()
        if row is not None and row.status in ("done", "failed"):
            vec = (row.result or {}).get("vec")
            return [float(x) for x in vec] if vec else None
        time.sleep(0.02)  # noqa: TID251
    with st.engine.begin() as c:
        c.execute(worker_task.delete().where(worker_task.c.id == tid, worker_task.c.status == "pending"))
    return None


def active_space(c: Any) -> Any:
    return c.execute(
        select(embedding_space)
        .where(embedding_space.c.status == "active")
        .order_by(embedding_space.c.id.desc())
        .limit(1)
    ).first()


# ---- filters ----
def _where(f: Filters, snapshot: str | None) -> tuple[list[Any], dict[str, Any]]:
    w: list[Any] = [search_doc.c.workspace_id == "default"]
    if f.tags_all:
        w.append(search_doc.c.tags.contains(f.tags_all))
    if f.category:
        w.append(search_doc.c.category == f.category)
    if f.taken_from:
        w.append(search_doc.c.taken_at >= datetime.fromisoformat(f.taken_from))
    if f.taken_to:
        w.append(search_doc.c.taken_at < datetime.fromisoformat(f.taken_to))
    if f.date_source:
        w.append(search_doc.c.date_source == f.date_source)
    if f.scope:
        keys = [
            f"src:{s.source_id}:/" + s.path.strip("/") if s.path.strip("/") else f"src:{s.source_id}:/" for s in f.scope
        ]
        w.append(search_doc.c.scope_keys.overlap(keys))
    if snapshot:
        w.append(search_doc.c.first_indexed_at <= datetime.fromisoformat(snapshot))
    return w, {}


def _ft(c: Any, where: list[Any], tsq: str, en: str) -> list[str]:
    cond = "tsv @@ CAST(:q AS tsquery)" + (" OR tsv @@ plainto_tsquery('english', :en)" if en else "")
    q = (
        select(search_doc.c.asset_id)
        .where(and_(*where), text(f"({cond})"))
        .order_by(text("ts_rank_cd(tsv, CAST(:q AS tsquery)) DESC"), search_doc.c.asset_id)
        .limit(PER_ROUTE)
    )
    return [r[0] for r in c.execute(q, {"q": tsq, "en": en})]


def run_search(st: AppState, body: SearchIn) -> dict[str, Any]:
    from opagent_search.tokenize import to_tsquery

    from .labels import tokenizer

    cur = _unsign(body.cursor) if body.cursor else None
    if cur and cur.get("h") != _qhash(body):
        raise HTTPException(400, detail={"code": "CURSOR_INVALID"})
    degraded: list[str] = []
    q = (body.q or "").strip()
    with st.engine.begin() as c:
        snapshot = cur["s"] if cur else str(c.execute(select(func.now())).scalar_one().isoformat())
        where, _ = _where(body.filters, snapshot)
        space = active_space(c)
    qvec = None
    if q and body.semantic:
        qvec = query_vector(st, q, space)
        if qvec is None:
            degraded.append("image_vector")
    routes: dict[str, list[str]] = {}
    mode = None
    with st.engine.begin() as c:
        if q:
            pq = tokenizer().query(q)
            if pq.words:
                hits = _ft(c, where, to_tsquery(pq.words), pq.english)
                mode = "words"
                if len(hits) < MIN_WORD_HITS and pq.bigram:
                    more = _ft(c, where, to_tsquery(pq.bigram), "")
                    if len(more) > len(hits):
                        hits, mode = list(dict.fromkeys(hits + more)), "bigram"
                routes["fulltext"] = hits
            if pq.tag_keys:
                routes["tag"] = [
                    r[0]
                    for r in c.execute(
                        select(search_doc.c.asset_id)
                        .where(and_(*where), search_doc.c.tags.overlap(pq.tag_keys))
                        .order_by(search_doc.c.confidence.desc().nulls_last(), search_doc.c.asset_id)
                        .limit(PER_ROUTE)
                    )
                ]
            if qvec is not None and space is not None:
                # exact scan over the pre-filtered set: an HNSW post-filter would return fewer (SRCH-SEM-004)
                vq = (
                    select(search_doc.c.asset_id)
                    .join(
                        asset_embedding,
                        and_(
                            asset_embedding.c.asset_id == search_doc.c.asset_id, asset_embedding.c.space_id == space.id
                        ),
                    )
                    .where(and_(*where))
                    .order_by(text("asset_embedding.vec <=> CAST(:qv AS vector)"), search_doc.c.asset_id)
                    .limit(PER_ROUTE)
                )
                routes["image_vector"] = [r[0] for r in c.execute(vq, {"qv": str(qvec)})]
            scores: dict[str, float] = {}
            matched: dict[str, list[str]] = {}
            for name, ids in routes.items():
                for rk, aid in enumerate(ids, 1):
                    scores[aid] = scores.get(aid, 0.0) + W[name] / (RRF_K + rk)
                    matched.setdefault(aid, []).append(name)
            ranked = sorted(scores, key=lambda a: (-scores[a], a))
        else:
            ranked = [
                r[0]
                for r in c.execute(
                    select(search_doc.c.asset_id)
                    .where(and_(*where))
                    .order_by(search_doc.c.taken_at.desc().nulls_last(), search_doc.c.asset_id)
                )
            ]
            scores, matched = {}, {a: ["filter"] for a in ranked}
        off = int(cur["o"]) if cur else 0
        page = ranked[off : off + body.limit]
        rows = (
            {r.asset_id: r for r in c.execute(select(search_doc).where(search_doc.c.asset_id.in_(page)))}
            if page
            else {}
        )
        facets = _facets(c, ranked if q else None, where, body.facets)
    items = [
        {
            "asset_id": a,
            "score": round(scores.get(a, 0.0), 6),
            "matched_by": matched.get(a, []),
            "category": rows[a].category,
            "tags": rows[a].tags,
            "caption": rows[a].caption,
            "taken_at": rows[a].taken_at.isoformat() if rows[a].taken_at else None,
        }
        for a in page
        if a in rows
    ]
    nxt = off + body.limit
    out: dict[str, Any] = {
        "items": items,
        "facets": facets,
        "next_cursor": _sign({"o": nxt, "s": snapshot, "h": _qhash(body)}) if nxt < len(ranked) else None,
        "total": len(ranked),
        "fulltext_mode": mode,
        "degraded": degraded,
    }
    if degraded:
        out["notice"] = "degraded_search"
    return out


def _facets(c: Any, ids: list[str] | None, where: list[Any], fields: list[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    scope = and_(*where) if ids is None else and_(*where, search_doc.c.asset_id.in_(ids or ["-"]))
    for f in fields:
        if f == "tags":
            rows = c.execute(
                select(func.unnest(search_doc.c.tags).label("t"), func.count()).where(scope).group_by(text("t"))
            ).all()
        else:
            col = {
                "category": search_doc.c.category,
                "year": search_doc.c.taken_year,
                "date_source": search_doc.c.date_source,
            }.get(f)
            if col is None:
                continue
            rows = c.execute(select(col, func.count()).where(scope).group_by(col)).all()
        out[f] = {str(k): int(n) for k, n in rows if k is not None}
    return out


@router.post("/search")
def search(body: SearchIn, req: Request) -> dict[str, Any]:
    return run_search(_st(req), body)


def index_status(c: Any) -> dict[str, Any]:
    space = active_space(c)
    total = c.execute(select(func.count()).select_from(search_doc)).scalar_one()
    have = 0
    if space is not None:
        have = c.execute(
            select(func.count()).select_from(asset_embedding).where(asset_embedding.c.space_id == space.id)
        ).scalar_one()
    return {"searchable": int(have), "total": int(total), "space": dict(space._mapping) if space else None}


@router.get("/search-index")
def search_index(req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        st = index_status(c)
    if st["space"]:
        st["space"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in st["space"].items()}
    return st
