"""Viewer endpoints (10 §2, §3.4; 02 §5.1 additions): library scan, timeline buckets, derived media.

The API serves only the derived cache (thumbnails/previews written by the Worker) and never opens originals
(invariant 1); originals stay behind the Worker relay / Tauri asset protocol.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from opagent_core.ids import uuid7
from opagent_db.schema import asset, job, job_item, result, source, worker_task
from pydantic import BaseModel
from sqlalchemy import func, insert, select, text, true

from .state import DEFAULT_WS, AppState

router = APIRouter()
UNKNOWN = "unknown"
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
IMMUTABLE = "public, max-age=31536000, immutable"


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def scan_task(st: AppState, s: Any, job_id: str | None, exclude_subtrees: list[str]) -> dict[str, Any]:
    return {
        "id": uuid7(st.clock),
        "kind": "scan",
        "job_id": job_id,
        "worker_id": s.worker_id,
        "payload": {
            "job_id": job_id,
            "source_id": s.id,
            "root_alias": s.root_alias,
            "sub_path": s.sub_path,
            "include": s.include,
            "exclude": s.exclude,
            "exclude_subtrees": exclude_subtrees,
        },
    }


@router.post("/sources/{source_id}/scan", status_code=202)
def scan_source(source_id: str, req: Request) -> dict[str, Any]:
    """Index a library folder for the viewer and search without organizing anything (onboarding step 3)."""
    st = _st(req)
    with st.engine.begin() as c:
        s = c.execute(select(source).where(source.c.id == source_id)).first()
        if s is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        from .preflight import enforce

        enforce(c, st, "scan", {"source_id": source_id})
        running = c.execute(
            select(worker_task.c.id).where(
                worker_task.c.kind == "scan",
                worker_task.c.job_id.is_(None),
                worker_task.c.payload["source_id"].astext == source_id,
                worker_task.c.status.in_(("queued", "leased")),
            )
        ).scalar()
        if running is not None:  # idempotent: one library scan per source at a time
            return {"task_id": running, "operation_id": running}
        t = scan_task(st, s, None, [])
        c.execute(insert(worker_task).values(**t))
    return {"task_id": t["id"], "operation_id": t["id"]}


@router.post("/sources/{source_id}/analyze", status_code=202)
def analyze_source(source_id: str, req: Request) -> dict[str, Any]:
    """Add a folder to the library: scan it, then label it. Labels only, so no file moves (ADR-0029, CORE-SM-005)."""
    from .preflight import ACTIVE_JOBS
    from .routes_public import JobIn, Target, start_job

    st = _st(req)
    with st.engine.begin() as c:
        s = c.execute(select(source).where(source.c.id == source_id)).first()
        if s is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        running = c.execute(
            select(job.c.id).where(
                job.c.kind == "label", job.c.params["source_id"].astext == source_id, job.c.status.in_(ACTIVE_JOBS)
            )
        ).scalar()
        if running is not None:  # idempotent: one analysis per folder at a time
            return {"job_id": running, "operation_id": running}
        body = JobIn(
            kind="label",
            source_id=source_id,
            preset="by-content-12",
            target=Target(root_alias=s.root_alias, sub_path=""),
        )
        jid = start_job(st, c, body)
    return {"job_id": jid, "operation_id": jid}


RECENT_RESULTS = 5
CURRENT_ITEMS = 4


@router.get("/analysis/status")
def analysis_status(req: Request, lang: str = "zh-TW") -> dict[str, Any]:
    """What the progress card shows beyond the numbers: each folder, the photos in hand, what the AI just found."""
    from .organize import preset
    from .preflight import ACTIVE_JOBS

    field = "zh-TW" if lang.startswith("zh") else "en"
    names = {c["key"]: c.get(field, c["key"]) for c in preset("by-content-12").categories}
    with _st(req).engine.connect() as c:
        rows = c.execute(
            select(job.c.id, job.c.status, job.c.params)
            .where(job.c.kind == "label", job.c.status.in_(ACTIVE_JOBS))
            .order_by(job.c.created_at)
        ).all()
        ids = [r.id for r in rows]
        counts: dict[str, dict[str, int]] = {}
        for jid, status, n in c.execute(
            select(job_item.c.job_id, job_item.c.status, func.count())
            .where(job_item.c.job_id.in_(ids))
            .group_by(job_item.c.job_id, job_item.c.status)
        ):
            counts.setdefault(jid, {})[status] = int(n)
        srcs = {
            s.id: "/".join(x for x in (s.root_alias, s.sub_path) if x)
            for s in c.execute(select(source.c.id, source.c.root_alias, source.c.sub_path))
        }
        jobs = []
        for r in rows:
            k = counts.get(r.id, {})
            # this run's photos, like the card's top line: the cached ones were analysed before (OP-023)
            total = sum(k.values()) - k.get("cached", 0)
            state = "queued" if r.status == "queued" else "analysing" if sum(k.values()) else "scanning"
            jobs.append(
                {
                    "job_id": r.id,
                    "folder": srcs.get((r.params or {}).get("source_id"), ""),
                    "state": state,
                    "done": k.get("done", 0),
                    "total": total,
                    "failed": k.get("failed", 0),
                }
            )
        current = c.execute(
            select(job_item.c.asset_id, job_item.c.rel, asset.c.content_hash)
            .join(asset, asset.c.id == job_item.c.asset_id)
            .where(job_item.c.job_id.in_(ids), job_item.c.status.in_(("leased", "running")))
            .order_by(job_item.c.seq)
            .limit(CURRENT_ITEMS)
        ).all()
        recent = c.execute(
            select(job_item.c.asset_id, job_item.c.rel, asset.c.content_hash, result.c.caption, result.c.category)
            .join(result, result.c.id == job_item.c.result_id)
            .join(asset, asset.c.id == job_item.c.asset_id)
            # during a run, this run's photos only: the run before's lingered while the model loaded (OP-024);
            # with nothing running, the latest results (IMP-030)
            .where(job_item.c.job_id.in_(ids) if ids else true(), job_item.c.status == "done")
            .order_by(result.c.created_at.desc(), result.c.id.desc())
            .limit(RECENT_RESULTS)
        ).all()
    return {
        "jobs": jobs,
        "current": [{"asset_id": r.asset_id, "ch": r.content_hash, "rel": r.rel} for r in current],
        "recent": [
            {
                "asset_id": r.asset_id,
                "ch": r.content_hash,
                "rel": r.rel,
                "caption": r.caption,
                "category": r.category,
                "category_name": names.get(r.category or ""),
            }
            for r in recent
        ],
    }


class RotationIn(BaseModel):
    degrees: Literal[0, 90, 180, 270]


@router.put("/assets/{asset_id}/rotation")
def set_rotation(asset_id: str, body: RotationIn, req: Request) -> dict[str, Any]:
    """Turn a photo in the app (ROT-001): a view setting kept in the library; the file is not touched (invariant 4)."""
    from sqlalchemy import update

    with _st(req).engine.begin() as c:
        n = c.execute(update(asset).where(asset.c.id == asset_id).values(rotation=body.degrees)).rowcount
    if not n:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return {"asset_id": asset_id, "rotation": body.degrees}


MEMORY_DAYS = 3  # 10 §2.8: the same day ±3, in earlier years
MEMORY_MAX = 24  # photos per year shown (10 §2.8: 12-30)
MEMORY_YEARS = 30  # years looked back at most (security review: one query per year)
MEMORY_PAST_YEARS = 100  # a requested day this far from the server's today is refused


def _memory_window(year: int, now: date) -> tuple[date, date]:
    from datetime import timedelta

    try:
        day = now.replace(year=year)
    except ValueError:  # 29 February in a year without one
        day = now.replace(year=year, day=28)
    return day - timedelta(days=MEMORY_DAYS), day + timedelta(days=MEMORY_DAYS + 1)


def _memory_total(c: Any, year: int, now: date) -> int:
    """All the photos of that window, not only those shown (MEM-006: 「共 24 張」 read as the whole)."""
    lo, hi = _memory_window(year, now)
    when = func.coalesce(asset.c.local_at, asset.c.taken_at)
    return int(
        c.execute(
            select(func.count()).select_from(asset).where(asset.c.workspace_id == DEFAULT_WS, when >= lo, when < hi)
        ).scalar_one()
    )


def _memory_year(c: Any, year: int, now: date) -> list[Any]:
    """The photos of one earlier year within ±3 days of today's date; one per near-duplicate group."""
    from datetime import timedelta

    from opagent_db.schema import duplicate_member

    try:
        day = now.replace(year=year)
    except ValueError:  # 29 February in a year without one
        day = now.replace(year=year, day=28)
    lo, hi = day - timedelta(days=MEMORY_DAYS), day + timedelta(days=MEMORY_DAYS + 1)
    when = func.coalesce(asset.c.local_at, asset.c.taken_at)
    hidden = select(duplicate_member.c.asset_id).where(duplicate_member.c.is_representative.is_(False))
    return list(
        c.execute(
            select(asset.c.id, asset.c.content_hash, asset.c.rotation, when.label("at"))
            .where(asset.c.workspace_id == DEFAULT_WS, when >= lo, when < hi, asset.c.id.notin_(hidden))
            .order_by(when, asset.c.id)
            .limit(MEMORY_MAX)
        ).all()
    )


@router.get("/memories/on-this-day")
def on_this_day(req: Request, today: str | None = None) -> dict[str, Any]:
    """那年今日 (10 §2.8, MEM-001): photos taken within ±3 days of today's date in each earlier year, newest year
    first, at most 30 years back; one photo per near-duplicate group. Read-only."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from .labels import LOCAL_TZ

    st = _st(req)
    server = st.clock.now().astimezone(ZoneInfo(LOCAL_TZ)).date()  # the local day, not UTC's (MEM-005)
    try:
        now = date.fromisoformat(today) if today else server
    except ValueError:
        raise HTTPException(422, detail={"code": "VALIDATION", "field": "today"}) from None
    if now > server + timedelta(days=1) or now.year < server.year - MEMORY_PAST_YEARS:  # MEM-004
        raise HTTPException(422, detail={"code": "VALIDATION", "field": "today"})
    when = func.coalesce(asset.c.local_at, asset.c.taken_at)
    groups = []
    with st.engine.connect() as c:
        first = c.execute(select(func.min(when)).where(asset.c.workspace_id == DEFAULT_WS)).scalar()
        if first is None:
            return {"today": now.isoformat(), "groups": []}
        oldest = max(first.year, now.year - MEMORY_YEARS)
        for year in range(now.year - 1, oldest - 1, -1):
            rows = _memory_year(c, year, now)
            if rows:
                groups.append(
                    {
                        "year": year,
                        "years_ago": now.year - year,
                        "total": _memory_total(c, year, now),
                        "photos": [
                            {
                                "asset_id": r.id,
                                "ch": r.content_hash,
                                "taken_at": r.at.isoformat() if r.at else None,
                                "rot": r.rotation,
                            }
                            for r in rows
                        ],
                    }
                )
    return {"today": now.isoformat(), "groups": groups}


THEME_OVERLAP = 0.5  # 「2025 的運動場」 shared 28 of 64 photos with 「2025 的足球」 (audit round 80)


@router.get("/memories/themes")
def memory_themes(req: Request, lang: str = "zh-TW") -> dict[str, Any]:
    """主題 (10 §2.8, MEM-010): a year's frequent subjects, 「2025 的足球」, from the photos' vocabulary tags. Computed
    when asked, like 那年今日 (ADR-0049); one photo per near-duplicate group. Read-only."""
    from opagent_core.memories import themes
    from opagent_db.schema import duplicate_member, search_doc

    from .search import tag_names

    when = func.coalesce(asset.c.local_at, asset.c.taken_at)
    hidden = select(duplicate_member.c.asset_id).where(duplicate_member.c.is_representative.is_(False))
    with _st(req).engine.connect() as c:
        rows = c.execute(
            select(
                asset.c.id,
                asset.c.content_hash,
                asset.c.rotation,
                when.label("at"),
                search_doc.c.tags,
                search_doc.c.caption,
            )
            .join(search_doc, search_doc.c.asset_id == asset.c.id)
            .where(asset.c.workspace_id == DEFAULT_WS, when.is_not(None))
            .order_by(when, asset.c.id)
        ).all()
        dup = {r[0] for r in c.execute(hidden)}
    info = {r.id: r for r in rows}
    from .labels import tokenizer

    # the count is what 「看全部」 finds, duplicates too; a theme mostly another's is not shown twice (MEM-013)
    found = themes(
        [(r.id, r.at.year, list(r.tags or [])) for r in rows],
        skip=tokenizer().not_themes(),
        max_overlap=THEME_OVERLAP,
        hidden=dup,
    )
    names = tag_names([t.key for t in found], lang)

    def cover_first(key: str, ids: tuple[str, ...]) -> list[str]:
        """The first photo whose caption names the subject leads: a staircase opened 「2026 的運動場」 (MEM-014)."""
        forms = [f for f in tokenizer()._forms_of(key) if f and not f.isascii()]
        lead = next((a for a in ids if any(f in (info[a].caption or "") for f in forms)), None)
        return [lead, *(a for a in ids if a != lead)] if lead else list(ids)

    return {
        "themes": [
            {
                "year": t.year,
                "key": t.key,
                "name": names.get(t.key, t.key),
                "count": t.count,
                "photos": [
                    {
                        "asset_id": a,
                        "ch": info[a].content_hash,
                        "taken_at": info[a].at.isoformat() if info[a].at else None,
                        "rot": info[a].rotation,
                    }
                    for a in cover_first(t.key, t.asset_ids)
                ],
            }
            for t in found
        ]
    }


EVENTS_MAX = 8
# what says nothing about one day: who is in it and indoors or out; a day of toys is a day of toys (audit round 99)
EVENT_SKIP = frozenset(
    {"people.child", "people.group", "people.teacher", "scene.indoor", "scene.outdoor", "scene.sky", "scene.classroom"}
    | {"activity.playing", "activity.study"}  # a classroom, playing: any school day (audit round 100 measure)
)


@router.get("/memories/events")
def memory_events(req: Request, lang: str = "zh-TW") -> dict[str, Any]:
    """活動 (MEM-016, ADR-0049 addendum 2): days of many photos, newest first, named by what most of them show.
    Computed when asked; one photo per near-duplicate group, so a count is what that day's search shows. Read-only."""
    from zoneinfo import ZoneInfo

    from opagent_core.memories import events
    from opagent_db.schema import duplicate_member, search_doc

    from .labels import LOCAL_TZ, tokenizer
    from .search import tag_names

    when = func.coalesce(asset.c.local_at, asset.c.taken_at)
    hidden = select(duplicate_member.c.asset_id).where(duplicate_member.c.is_representative.is_(False))
    with _st(req).engine.connect() as c:
        rows = c.execute(
            select(
                asset.c.id,
                asset.c.content_hash,
                asset.c.rotation,
                when.label("at"),
                search_doc.c.tags,
                search_doc.c.caption,
            )
            .join(search_doc, search_doc.c.asset_id == asset.c.id)
            .where(asset.c.workspace_id == DEFAULT_WS, when.is_not(None), asset.c.id.notin_(hidden))
            .order_by(when, asset.c.id)
        ).all()
    tz = ZoneInfo(LOCAL_TZ)
    info = {r.id: r for r in rows}
    forms: dict[str, list[str]] = {}

    def names_subject(key: str | None, a: str) -> bool:
        """The photo's caption names the subject: it leads the card (MEM-019; MEM-014 for themes)."""
        if not key:
            return False
        if key not in forms:
            forms[key] = [f for f in tokenizer()._forms_of(key) if f and not f.isascii()]
        return any(f in (info[a].caption or "") for f in forms[key])

    found = events(
        [(r.id, r.at.astimezone(tz).date() if r.at.tzinfo else r.at.date(), list(r.tags or [])) for r in rows],
        skip=EVENT_SKIP,
        named_only=True,  # an import day of unrelated photos is no outing (MEM-018)
        prefer=lambda a, key: names_subject(key, a),
    )[:EVENTS_MAX]

    names = tag_names([e.key for e in found if e.key], lang)
    return {
        "events": [
            {
                "day": e.day.isoformat(),
                "last": e.last.isoformat() if e.last else None,
                "key": e.key,
                "name": names.get(e.key, e.key) if e.key else None,
                "count": e.count,
                "photos": [
                    {
                        "asset_id": a,
                        "ch": info[a].content_hash,
                        "rot": info[a].rotation,
                        "taken_at": info[a].at.isoformat(),
                    }
                    for a in e.asset_ids
                ],
            }
            for e in found
        ]
    }


@router.get("/timeline/buckets")
def buckets(req: Request) -> dict[str, Any]:
    """Per-month counts, newest first; photos with no usable date go to ``unknown`` (10 §2.3)."""
    with _st(req).engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT to_char(month_at, 'YYYY-MM') AS m, count(*) AS n FROM asset "
                "WHERE workspace_id = :ws GROUP BY month_at ORDER BY month_at DESC NULLS LAST"
            ),
            {"ws": DEFAULT_WS},
        ).all()
    return {"buckets": [{"month": r.m or UNKNOWN, "count": r.n} for r in rows]}


@router.get("/timeline/buckets/{month}")
def bucket(month: str, req: Request) -> dict[str, Any]:
    """Layout data for one month: ``{id, w, h, thumbhash, taken_at, flags}`` (10 §2.2)."""
    if month != UNKNOWN and not _MONTH.match(month):
        raise HTTPException(422, detail={"code": "VALIDATION", "field": "month"})
    q = select(
        asset.c.id,
        asset.c.width,
        asset.c.height,
        asset.c.thumbhash,
        asset.c.local_at,
        asset.c.date_source,
        asset.c.content_hash,
        asset.c.rotation,
    ).where(asset.c.workspace_id == DEFAULT_WS)
    if month == UNKNOWN:
        q = q.where(asset.c.month_at.is_(None)).order_by(asset.c.id)
    else:
        y, m = (int(x) for x in month.split("-"))
        q = q.where(asset.c.month_at == date(y, m, 1))
        q = q.order_by(asset.c.local_at.desc(), asset.c.id)
    with _st(req).engine.connect() as c:
        rows = c.execute(q).all()
    return {
        "month": month,
        "assets": [
            {
                "id": r.id,
                # a quarter turn lays the photo out in its new shape (ROT-001)
                "w": r.height if r.rotation in (90, 270) else r.width,
                "h": r.width if r.rotation in (90, 270) else r.height,
                "rot": r.rotation,
                "thumbhash": r.thumbhash,
                "ch": r.content_hash,  # desktop: thumbnail path in the local cache (10 §5.4)
                "taken_at": r.local_at.isoformat() if r.local_at else None,
                "flags": ["date_from_mtime"] if r.date_source == "mtime" else [],
            }
            for r in rows
        ],
    }


PREVIEW_WAIT_S = 8.0


def _make_preview(st: AppState, asset_id: str, h: str) -> None:
    """Ask the worker for a 2048 px preview of one photo, written to its thumbnail cache; the API opens no original
    (invariant 1). One task per photo: requests while it is pending wait on it, and a photo whose preview failed (a
    video, an unreadable file) is not asked again (VIEW-036, security review). Nothing is raised: the caller falls
    back to the 1024 px preview."""
    import time

    from opagent_db.schema import asset_location

    with st.engine.begin() as c:
        last = c.execute(
            select(worker_task.c.id, worker_task.c.status)
            .where(worker_task.c.kind == "preview", worker_task.c.payload["content_hash"].astext == h)
            .order_by(worker_task.c.created_at.desc())
            .limit(1)
        ).first()
        if last is not None and last.status == "failed":
            return
        tid = last.id if last is not None and last.status in ("pending", "leased") else None
        if tid is None:
            loc = c.execute(
                select(source.c.worker_id, source.c.root_alias, asset_location.c.rel_path)
                .join(asset_location, asset_location.c.source_id == source.c.id)
                .where(asset_location.c.asset_id == asset_id, asset_location.c.missing.is_(False))
                .limit(1)
            ).first()
            if loc is None or loc.worker_id is None:
                return
            tid = uuid7(st.clock)
            c.execute(
                insert(worker_task).values(
                    id=tid,
                    kind="preview",
                    worker_id=loc.worker_id,
                    payload={"content_hash": h, "root_alias": loc.root_alias, "rel": loc.rel_path, "size": 2048},
                )
            )
    deadline = time.monotonic() + PREVIEW_WAIT_S  # noqa: TID251 - waits on a real worker over the wire
    while time.monotonic() < deadline:  # noqa: TID251
        with st.engine.connect() as c:
            status = c.execute(select(worker_task.c.status).where(worker_task.c.id == tid)).scalar()
        if status in ("done", "failed"):
            return
        time.sleep(0.05)  # noqa: TID251


def _edge_relay(st: AppState, asset_id: str, h: str, variant: str) -> Response | None:
    """edge-thumbs (02 §1.4): the thumbnail stays on the worker; fetch it through the worker channel, never store it."""
    if not st.multi_user:
        return None
    import base64

    from opagent_db.schema import asset_location, source

    from .routes_public import worker_rpc
    from .routes_remote import egress_policy

    with st.engine.begin() as c:
        if egress_policy(st, c)["mode"] != "edge-thumbs":
            return None
        wid = c.execute(
            select(source.c.worker_id)
            .join(asset_location, asset_location.c.source_id == source.c.id)
            .where(asset_location.c.asset_id == asset_id, asset_location.c.missing.is_(False))
            .limit(1)
        ).scalar()
    if wid is None:
        return None
    res = worker_rpc(st, wid, "thumb_fetch", {"content_hash": h, "variant": variant}, timeout_s=10.0)
    if not res or "thumbnail_b64" not in res:
        return None
    return Response(
        base64.b64decode(res["thumbnail_b64"]), media_type="image/webp", headers={"Cache-Control": "no-store"}
    )


@router.get("/assets/{asset_id}/media")
def media(asset_id: str, req: Request, variant: Literal["t256", "t512", "p1024", "p2048"] = Query(...)) -> FileResponse:
    """Derived variants from the Worker's thumbnail cache, content-addressed and therefore immutable (10 §3.4)."""
    st = _st(req)
    with st.engine.connect() as c:
        h = c.execute(select(asset.c.content_hash).where(asset.c.id == asset_id)).scalar()
    if h is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    if st.thumbs_dir is None:
        raise HTTPException(404, detail={"code": "THUMB_MISSING"})
    p = st.thumbs_dir / h[:2] / f"{h}.{variant}.webp"
    if not p.is_file() and variant == "p2048":
        _make_preview(st, asset_id, h)  # on demand, the first time it is zoomed (10 §3.4, VIEW-035)
    if not p.is_file():
        relayed = _edge_relay(st, asset_id, h, variant)
        if relayed is not None:
            return relayed  # type: ignore[return-value]
        raise HTTPException(404, detail={"code": "THUMB_MISSING"})
    return FileResponse(p, media_type="image/webp", headers={"Cache-Control": IMMUTABLE, "ETag": f'"{h}.{variant}"'})


@router.get("/presets/{key}")
def get_preset(key: str, lang: str = "en") -> dict[str, Any]:
    """Display names of a built-in organize preset's categories (Lightbox correction, review, wizard)."""
    from .organize import PRESETS_DIR, preset

    if not re.fullmatch(r"[a-z0-9-]+", key) or not (PRESETS_DIR / f"{key}.yaml").is_file():
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    p = preset(key)
    loc = "zh-TW" if lang.lower().startswith("zh") else "en"
    return {
        "key": key,
        "categories": [
            {
                "key": c["key"],
                "name": c.get(loc) or c.get("en") or c["key"],
                "description": (c.get("description") or {}).get(loc, ""),
            }
            for c in p.categories
        ],
    }


@router.get("/map/points")
def map_points(req: Request) -> dict[str, Any]:
    """Photos with a GPS position (10 §2.5): ``{id, lat, lon}``; the rest are counted as "no location"."""
    with _st(req).engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, content_hash, (exif->'gps'->>'lat')::float AS lat, (exif->'gps'->>'lon')::float AS lon "
                "FROM asset "
                "WHERE workspace_id = :ws ORDER BY id"
            ),
            {"ws": DEFAULT_WS},
        ).all()
    # ch: the desktop app builds thumbnail URLs from the content hash (MAP-012)
    pts = [
        {"id": r.id, "ch": r.content_hash, "lat": r.lat, "lon": r.lon}
        for r in rows
        if r.lat is not None and r.lon is not None
    ]
    return {"points": pts, "without_location": len(rows) - len(pts)}
