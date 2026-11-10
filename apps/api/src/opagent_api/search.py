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
import re
import secrets
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from opagent_db.schema import asset, asset_embedding, embedding_space, search_doc, worker, worker_task
from pydantic import BaseModel, Field
from sqlalchemy import and_, bindparam, false, func, or_, select, text

from .state import AppState

router = APIRouter()
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
    tags_any: list[str] | None = None
    tags_none: list[str] | None = None
    category: str | None = None
    categories: list[str] | None = None
    taken_from: str | None = None
    taken_to: str | None = None
    date_source: str | None = None
    scope: list[Scope] | None = None
    # an MCP token's folders (05 §3.2): ANDed with everything else, so a caller can only narrow it (MCP-FOLD-001)
    within: list[Scope] | None = None
    persons_all: list[str] | None = None  # 07 §3.3: only with faces on (403 FACE_SCOPE_REQUIRED otherwise)
    persons_any: list[str] | None = None
    persons_none: list[str] | None = None


class SearchIn(BaseModel):
    q: str | None = None
    filters: Filters = Field(default_factory=Filters)
    semantic: bool = True
    limit: int = Field(default=50, ge=1, le=500)
    cursor: str | None = None
    facets: list[str] = []
    collapse_duplicates: bool = True  # one representative per near-duplicate group, with "+N" (05 §2.1)
    plan: bool = False  # run the rule-based planner on q (05 §2.3): NL dates / tags → filters, rest stays text
    lang: str = "zh-TW"  # the language of tag_names (IMP-022)


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


def query_vector(st: AppState, text_q: str, space: Any, modality: str = "image") -> list[float] | None:
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
                payload={
                    "text": text_q,
                    "fingerprint": space.fingerprint,
                    "modality": modality,
                    "model": space.model_key,
                },
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


def vector_floor(st: AppState, space: Any) -> float | None:
    """The cosine under which a neighbour is not a match (ADR-0027). It sits in the catalog, per model: scores mean
    different things in different spaces, and it applies at query time, so tuning it costs no re-embedding."""
    m = st.registry().models.get(str(space.fingerprint).split("@")[0])
    floor = ((m.spec.embedding or {}) if m else {}).get("min_cosine")
    return float(floor) if floor is not None else None


def active_space(c: Any) -> Any:
    return c.execute(
        select(embedding_space)
        .where(embedding_space.c.status == "active", embedding_space.c.modality == "image")
        .order_by(embedding_space.c.id.desc())
        .limit(1)
    ).first()


# ---- filters ----
def _where(f: Filters, snapshot: str | None) -> tuple[list[Any], dict[str, Any]]:
    w: list[Any] = [search_doc.c.workspace_id == "default"]
    if f.tags_all:
        w.append(search_doc.c.tags.contains(f.tags_all))
    if f.tags_any:
        w.append(search_doc.c.tags.overlap(f.tags_any))
    if f.tags_none:
        w.append(~search_doc.c.tags.overlap(f.tags_none))
    if f.category:
        w.append(search_doc.c.category == f.category)
    if f.categories:
        w.append(search_doc.c.category.in_(f.categories))
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
    if f.persons_all:
        w.append(search_doc.c.person_ids.contains(f.persons_all))
    if f.persons_any:
        w.append(search_doc.c.person_ids.overlap(f.persons_any))
    if f.persons_none:
        w.append(or_(search_doc.c.person_ids.is_(None), ~search_doc.c.person_ids.overlap(f.persons_none)))
    if f.within is not None:
        keys = [
            f"src:{s.source_id}:/" + s.path.strip("/") if s.path.strip("/") else f"src:{s.source_id}:/"
            for s in f.within
        ]
        w.append(search_doc.c.scope_keys.overlap(keys or ["-"]))
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


MAX_CONCEPTS = 5


def _concept_cond(i: int, cpt: Any) -> Any:
    """A photo shows a concept when its text (caption, tags, any name of the term) or its tag id says so; a Chinese
    word the vocabulary does not know also matches by its character pairs, for captions segmented differently."""
    from opagent_search.tokenize import to_tsquery, to_tsquery_alts

    from .labels import tokenizer

    tok = tokenizer()
    latin = cpt.word.isascii()
    parts = [
        _ft_cond(
            to_tsquery_alts([cpt.forms[0]], {cpt.forms[0]: list(cpt.forms[1:])}), cpt.word if latin else "", f"c{i}"
        )
    ]
    if cpt.key:
        parts.append(search_doc.c.tags.overlap([cpt.key]))
    elif not latin:
        bi = tok.bigrams(cpt.word)
        if bi:
            parts.append(_ft_cond(to_tsquery(bi), "", f"cb{i}"))
    return or_(*parts)


def _all_concepts(c: Any, where: list[Any], concepts: list[Any]) -> tuple[list[Any], list[str]]:
    """The match set of a query with several concepts (ADR-0032): every concept must show. A word the vocabulary does
    not know and no photo shows is set aside and reported, so 「小朋友 量子」 still answers with the children."""
    concepts = concepts[:MAX_CONCEPTS]
    conds = [(cpt, _concept_cond(i, cpt)) for i, cpt in enumerate(concepts)]
    required = [cond for cpt, cond in conds if cpt.key]
    relaxed: list[str] = []
    for cpt, cond in conds:
        if cpt.key:
            continue
        n = c.execute(select(func.count()).select_from(search_doc).where(and_(*where, *required, cond))).scalar_one()
        if n:
            required.append(cond)
        else:
            relaxed.append(cpt.word)
    return required, relaxed


def _ft_cond(tsq: str, en: str, tag: str) -> Any:
    cond = f"tsv @@ CAST(:q_{tag} AS tsquery)" + (f" OR tsv @@ plainto_tsquery('english', :en_{tag})" if en else "")
    params: list[Any] = [bindparam(f"q_{tag}", tsq)] + ([bindparam(f"en_{tag}", en)] if en else [])
    return text(f"({cond})").bindparams(*params)


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
UNINDEXED = ("place", "camera_make", "persons_all")  # planner filters the search index cannot apply yet


def _apply_plan(st: AppState, body: SearchIn, q: str) -> tuple[dict[str, Any], SearchIn]:
    from zoneinfo import ZoneInfo

    from opagent_geo.gazetteer import Gazetteer
    from opagent_search.planner import plan

    tz = ZoneInfo("Asia/Taipei")
    p = plan(q, today=st.clock.now().astimezone(tz).date(), tz=tz, gazetteer=Gazetteer.default())
    if p["planner"]["confidence"] < LLM_PLAN_BELOW:
        llm = _llm_plan(st, q)
        if llm is not None:
            p = llm
    f = body.filters.model_copy()
    pf = p["filters"]
    if "taken_at" in pf and not (f.taken_from or f.taken_to):
        f.taken_from, f.taken_to = pf["taken_at"]["from"], pf["taken_at"]["to"]
    # tag words stay in the query text, where the tag route (R4) ranks by them; as a hard filter they would cut
    # the image-vector route's recall (a photo of a dog that nobody tagged "dog" would vanish)
    flat = {**p["planner"], "filters": pf, "text": p["text"], "unapplied": [k for k in UNINDEXED if k in pf]}
    return flat, body.model_copy(update={"filters": f, "q": p["text"]})


LLM_PLAN_BELOW = 0.6  # rule confidence under which the worker's LLM is asked (05 §2.3)
LLM_PLAN_TIMEOUT_S = 1.5


def _llm_plan(st: AppState, q: str) -> dict[str, Any] | None:
    """``query.plan`` RPC on the interactive lane: structured output from the loaded VLM/LLM; ``None`` when no
    worker answers within 1.5 s or it cannot plan (SRCH-PLAN-011) — the rules result is used then."""
    import uuid

    with st.engine.begin() as c:
        w = _worker_online(c, st)
        if w is None:
            return None
        tid = "rpc-" + uuid.uuid4().hex[:12]
        c.execute(
            worker_task.insert().values(id=tid, kind="query_plan", lane="interactive", worker_id=w.id, payload={"q": q})
        )
    deadline = time.monotonic() + LLM_PLAN_TIMEOUT_S  # noqa: TID251 - waits on a real worker over the wire
    while time.monotonic() < deadline:  # noqa: TID251
        with st.engine.begin() as c:
            row = c.execute(select(worker_task.c.status, worker_task.c.result).where(worker_task.c.id == tid)).first()
        if row is not None and row.status in ("done", "failed"):
            res = row.result or {}
            if row.status == "done" and isinstance(res.get("filters"), dict) and isinstance(res.get("text"), str):
                return {
                    "text": res["text"],
                    "filters": res["filters"],
                    "planner": {"source": "llm", **res.get("planner", {})},
                }
            return None
        time.sleep(0.02)  # noqa: TID251
    return None


def run_search(st: AppState, body: SearchIn) -> dict[str, Any]:
    from opagent_search.tokenize import to_tsquery, to_tsquery_alts

    from .labels import tokenizer

    # control characters carry no meaning in a query, and PostgreSQL cannot store NUL in text/JSON (SRCH-TOK-009)
    body = body.model_copy(update={"q": _CONTROL.sub("", body.q)}) if body.q else body
    f0 = body.filters
    if f0.persons_all or f0.persons_any or f0.persons_none:
        from .faces import fsettings

        with st.engine.begin() as c:
            if not fsettings(c).get("enabled"):  # refuse rather than silently answer "nobody" (07 §3.3)
                raise HTTPException(403, detail={"code": "FACE_SCOPE_REQUIRED"})
    cur = _unsign(body.cursor) if body.cursor else None
    if cur and cur.get("h") != _qhash(body):
        raise HTTPException(400, detail={"code": "CURSOR_INVALID"})
    degraded: list[str] = []
    q = (body.q or "").strip()
    planned: dict[str, Any] | None = None
    if body.plan and q:
        planned, body = _apply_plan(st, body, q)
        q = (body.q or "").strip()
    with st.engine.begin() as c:
        snapshot = cur["s"] if cur else str(c.execute(select(func.now())).scalar_one().isoformat())
        where, _ = _where(body.filters, snapshot)
        if body.collapse_duplicates:  # pre-filter, so total / facets / selection agree (ADR-0005)
            from opagent_db.schema import duplicate_member

            hidden = select(duplicate_member.c.asset_id).where(duplicate_member.c.is_representative.is_(False))
            where.append(search_doc.c.asset_id.notin_(hidden))
        space = active_space(c)
        from .routes_caption import caption_space

        cspace = caption_space(c)
    qvec = None
    cvec = None
    capped = False
    if q and body.semantic and cspace is not None:
        cvec = query_vector(st, q, cspace, modality="caption")
        if cvec is None:
            degraded.append("caption_vector")
    vq_text = tokenizer().vector_text(q) if q else None  # None: skipped on purpose, not a fault (ADR-0031)
    if vq_text and body.semantic:
        qvec = query_vector(st, vq_text, space)
        if qvec is None:
            degraded.append("image_vector")
    routes: dict[str, list[str]] = {}
    relaxed: list[str] = []
    concepts: list[Any] = []
    member: list[Any] = []  # ADR-0005: the match set (total, facets, selection) — not just the ranking window
    mode = None
    with st.engine.begin() as c:
        if q:
            pq = tokenizer().query(q)
            if pq.words:
                hits = _ft(c, where, to_tsquery_alts(pq.words, pq.alts), pq.english)
                mode = "words"
                member.append(_ft_cond(to_tsquery_alts(pq.words, pq.alts), pq.english, "w"))
                if len(hits) < MIN_WORD_HITS and pq.bigram:
                    more = _ft(c, where, to_tsquery(pq.bigram), "")
                    if len(more) > len(hits):
                        hits, mode = list(dict.fromkeys(hits + more)), "bigram"
                        member.append(_ft_cond(to_tsquery(pq.bigram), "", "b"))
                routes["fulltext"] = hits
            if pq.tag_keys:
                member.append(search_doc.c.tags.overlap(pq.tag_keys))
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
                    select(search_doc.c.asset_id, text("1 - (asset_embedding.vec <=> CAST(:qv AS vector)) AS cos"))
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
                near = c.execute(vq, {"qv": str(qvec)}).all()
                floor = vector_floor(st, space)
                routes["image_vector"] = [r[0] for r in near if floor is None or r[1] >= floor]
                capped = len(routes["image_vector"]) == PER_ROUTE
            if cvec is not None and cspace is not None:  # R2: multilingual caption vectors (05 §2, ADR-0014)
                cq = (
                    select(search_doc.c.asset_id)
                    .join(
                        asset_embedding,
                        and_(
                            asset_embedding.c.asset_id == search_doc.c.asset_id,
                            asset_embedding.c.space_id == cspace.id,
                        ),
                    )
                    .where(and_(*where))
                    .order_by(text("asset_embedding.vec <=> CAST(:qv AS vector)"), search_doc.c.asset_id)
                    .limit(PER_ROUTE)
                )
                routes["caption_vector"] = [r[0] for r in c.execute(cq, {"qv": str(cvec)})]
            concepts = tokenizer().concepts(q)
            if len(concepts) > 1:  # every concept must show (ADR-0032): one tag or one vector hit is not enough
                member, relaxed = _all_concepts(c, where, concepts)
            from opagent_search.fusion import fuse, route_weights

            fused = fuse(routes, route_weights(q))
            scores = {f.asset_id: f.score for f in fused}
            matched = {f.asset_id: f.matched_by for f in fused}
            ranked = [f.asset_id for f in fused]
            vec_ids = (routes.get("image_vector") or []) + (routes.get("caption_vector") or [])
            if len(concepts) > 1:
                match = and_(*member) if member else false()
                # the image model was asked the whole question (English, or every concept in the vocabulary): what it
                # finds above the floor shows all of it together, "a dog on the beach" (SRCH-SEM-001, ADR-0032)
                whole = all(cpt.key or cpt.word.isascii() for cpt in concepts)
                if whole and vec_ids:
                    match = or_(match, search_doc.c.asset_id.in_(vec_ids))
                # the routes rank; only photos showing every concept stay (vectors never add one, ADR-0032)
                keep = {
                    r[0]
                    for r in c.execute(
                        select(search_doc.c.asset_id).where(search_doc.c.asset_id.in_(ranked or ["-"]), match)
                    )
                }
                ranked = [a for a in ranked if a in keep]
            else:
                if vec_ids:
                    member.append(search_doc.c.asset_id.in_(vec_ids))
                match = or_(*member) if member else false()
            tail_q = (
                select(search_doc.c.asset_id)
                .where(and_(*where), match, search_doc.c.asset_id.notin_(ranked or ["-"]))
                .order_by(search_doc.c.taken_at.desc().nulls_last(), search_doc.c.asset_id)
            )
            n_tail = c.execute(select(func.count()).select_from(tail_q.subquery())).scalar_one()
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
        total = len(ranked)
        if q:
            total = len(ranked) + n_tail
            if len(page) < body.limit and off + len(page) < total:  # continue into the stable tail of the match set
                start = max(0, off - len(ranked))
                page += [r[0] for r in c.execute(tail_q.offset(start).limit(body.limit - len(page)))]
        rows = (
            {r.asset_id: r for r in c.execute(select(search_doc).where(search_doc.c.asset_id.in_(page)))}
            if page
            else {}
        )
        facets = _facets(c, None, [*where, match] if q else where, body.facets)
        # ADR-0027 as amended 2026-11-10 (the owner's decision): photos below the floor are not offered as "possibly
        # related" any more; 「夜景」 with no night photos showed 24 unrelated ones. The field stays, always empty.
        near_ids: list[str] = []
        rel_rows = (
            {r.asset_id: r for r in c.execute(select(search_doc).where(search_doc.c.asset_id.in_(near_ids)))}
            if near_ids
            else {}
        )
        hashes = (
            dict(c.execute(select(asset.c.id, asset.c.content_hash).where(asset.c.id.in_([*page, *near_ids]))).all())
            if page or near_ids
            else {}
        )
    items = [
        {
            "asset_id": a,
            "ch": hashes.get(a),  # desktop thumbnails by content hash (10 §5.4)
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
    if body.collapse_duplicates and items:
        from opagent_db.schema import duplicate_member

        dm = duplicate_member.alias("dm")
        with st.engine.begin() as c:
            counts: dict[str, int] = dict(
                c.execute(
                    select(duplicate_member.c.asset_id, func.count(dm.c.asset_id) - 1)
                    .join(dm, dm.c.group_id == duplicate_member.c.group_id)
                    .where(duplicate_member.c.asset_id.in_([i["asset_id"] for i in items]))
                    .group_by(duplicate_member.c.asset_id)
                ).all()
            )
        for i in items:
            if counts.get(i["asset_id"]):
                i["duplicates"] = int(counts[i["asset_id"]])
    nxt = off + body.limit
    out: dict[str, Any] = {
        "items": items,
        "facets": facets,
        "tag_names": tag_names(list(facets.get("tags") or {}), body.lang),
        "next_cursor": _sign({"o": nxt, "s": snapshot, "h": _qhash(body)}) if nxt < total else None,
        "total": total,
        "relaxed": relaxed,  # words no photo shows, left out so the rest still answers (ADR-0032)
        "total_capped": capped,  # the vector route hit K: `total` is a lower bound
        "related": [
            {"asset_id": a, "ch": hashes.get(a), "caption": rel_rows[a].caption} for a in near_ids if a in rel_rows
        ],
        "fulltext_mode": mode,
        "degraded": degraded,
    }
    if degraded:
        out["notice"] = "degraded_search"
    if planned is not None:
        out["planner"] = planned  # shown as removable filter chips (05 §2.3)
    return out


def tag_names(keys: list[str], lang: str) -> dict[str, str]:
    """Display names for vocabulary ids; a free tag is already its own name (IMP-022)."""
    from .labels import tokenizer

    tok = tokenizer()
    return {k: n for k in keys if (n := tok.term_name(k, lang)) is not None}


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


MAX_UPLOAD = 30 * 1024 * 1024


@router.post("/search/by-image")
async def search_by_image(req: Request) -> dict[str, Any]:
    """Search with an uploaded picture (05 §2.2). The bytes live only in this process's memory: the worker fetches
    them once by token over the worker channel; nothing reaches the database, the data dir or any cache."""
    import secrets

    from starlette.concurrency import run_in_threadpool

    form = await req.form()
    up = form.get("file")
    if up is None or isinstance(up, str):
        raise HTTPException(400, detail={"code": "FILE_REQUIRED"})
    data = await up.read()
    if not data or len(data) > MAX_UPLOAD:
        raise HTTPException(400, detail={"code": "FILE_TOO_LARGE" if data else "FILE_REQUIRED"})
    k = max(1, min(int(str(form.get("k") or 20)), 200))
    st = _st(req)
    token = secrets.token_urlsafe(24)
    st.blobs[token] = data
    try:
        return await run_in_threadpool(_by_image, st, token, k)
    finally:
        st.blobs.pop(token, None)


def _by_image(st: AppState, token: str, k: int) -> dict[str, Any]:
    import uuid

    with st.engine.begin() as c:
        space = active_space(c)
        w = _worker_online(c, st)
        if space is None or w is None:
            return {"items": [], "degraded": ["image_vector"], "notice": "degraded_search"}
        tid = "rpc-" + uuid.uuid4().hex[:12]
        c.execute(
            worker_task.insert().values(
                id=tid,
                kind="embed_image",
                lane="interactive",
                worker_id=w.id,
                payload={"token": token, "fingerprint": space.fingerprint},
            )
        )
    vec = None
    deadline = time.monotonic() + RPC_TIMEOUT_S * 3  # noqa: TID251 - waits on a real worker over the wire
    while time.monotonic() < deadline:  # noqa: TID251
        with st.engine.begin() as c:
            row = c.execute(select(worker_task.c.status, worker_task.c.result).where(worker_task.c.id == tid)).first()
        if row is not None and row.status in ("done", "failed"):
            v = (row.result or {}).get("vec")
            vec = [float(x) for x in v] if v else None
            break
        time.sleep(0.02)  # noqa: TID251
    if vec is None:
        return {"items": [], "degraded": ["image_vector"], "notice": "degraded_search"}
    with st.engine.begin() as c:
        rows = c.execute(
            select(
                asset_embedding.c.asset_id,
                (1 - asset_embedding.c.vec.op("<=>")(text("CAST(:qv AS vector)"))).label("s"),
            )
            .where(asset_embedding.c.space_id == space.id)
            .order_by(text("s DESC"), asset_embedding.c.asset_id)
            .limit(k),
            {"qv": str(vec)},
        ).all()
    return {"items": [{"asset_id": a, "score": round(float(sc), 6), "matched_by": ["image_vector"]} for a, sc in rows]}


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


class EmbeddingsIn(BaseModel):
    worker_id: str
    model_key: str
    fingerprint: str
    dim: int
    items: list[dict[str, Any]]  # {content_hash, vec}


worker_router = APIRouter()


@worker_router.get("/blobs/{token}")
def fetch_blob(token: str, req: Request) -> Any:
    """An uploaded query image, handed to the worker once and then forgotten (SRCH-IMG-002)."""
    from fastapi.responses import Response as Raw

    data = _st(req).blobs.pop(token, None)
    if data is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return Raw(content=data, media_type="application/octet-stream")


@worker_router.post("/embeddings")
def ingest_embeddings(body: EmbeddingsIn, req: Request) -> dict[str, int]:
    """Image vectors from the Worker (05 §1.1): one space per model fingerprint; vectors keyed by content hash."""
    from opagent_db.schema import asset
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    st = _st(req)
    with st.engine.begin() as c:
        c.execute(
            pg_insert(embedding_space)
            .values(model_key=body.model_key, fingerprint=body.fingerprint, dim=body.dim)
            .on_conflict_do_nothing(index_elements=["fingerprint"])
        )
        sid = c.execute(
            select(embedding_space.c.id).where(embedding_space.c.fingerprint == body.fingerprint)
        ).scalar_one()
        n = 0
        for it in body.items:
            ids = [r[0] for r in c.execute(select(asset.c.id).where(asset.c.content_hash == it["content_hash"]))]
            for aid in ids:
                c.execute(
                    text(
                        "INSERT INTO asset_embedding(asset_id, space_id, vec, source_hash) "
                        "VALUES (:a, :s, CAST(:v AS vector), :h) ON CONFLICT (asset_id, space_id) "
                        "DO UPDATE SET vec = EXCLUDED.vec, source_hash = EXCLUDED.source_hash"
                    ),
                    {"a": aid, "s": sid, "v": str([float(x) for x in it["vec"]]), "h": it["content_hash"]},
                )
                n += 1
    return {"stored": n}


def missing_vectors(c: Any) -> dict[str, list[dict[str, str]]]:
    """Per worker, what has no vector in the active space."""
    from opagent_db.schema import asset, asset_location, source

    space = active_space(c)
    have = select(asset_embedding.c.asset_id)
    if space is not None:
        have = have.where(asset_embedding.c.space_id == space.id)
    rows = c.execute(
        select(asset.c.content_hash, asset_location.c.rel_path, source.c.root_alias, source.c.worker_id)
        .join(asset_location, asset_location.c.asset_id == asset.c.id)
        .join(source, source.c.id == asset_location.c.source_id)
        .where(asset.c.id.not_in(have), asset_location.c.missing.is_(False))
    ).all()
    by_worker: dict[str, list[dict[str, str]]] = {}
    for h, rel, alias, wid in rows:
        by_worker.setdefault(wid, []).append({"content_hash": h, "rel": rel, "root_alias": alias})
    return by_worker


@router.post("/search-index/reindex", status_code=202)
def reindex(req: Request) -> dict[str, Any]:
    """Queue embedding for every asset without a vector in the active space (auto_fix ``reindex``, PF-053)."""
    from opagent_core.ids import uuid7

    st = _st(req)
    with st.engine.begin() as c:
        by_worker = missing_vectors(c)
        tids = []
        for wid, items in by_worker.items():
            tid = uuid7(st.clock)
            c.execute(worker_task.insert().values(id=tid, kind="embed_assets", worker_id=wid, payload={"items": items}))
            tids.append(tid)
    return {"queued": sum(len(v) for v in by_worker.values()), "tasks": tids}
