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
from pydantic import BaseModel, Field
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


def _memory_months(c: Any, oldest: int, now: date) -> dict[int, tuple[list[Any], int]]:
    """The same month of each earlier year, in one query (MEM-026): shown when no photo is within ±3 days of today in
    any year; per year its photos (one per near-duplicate group, at most MEMORY_MAX) and how many there are."""
    from opagent_db.schema import duplicate_member

    when = func.coalesce(asset.c.local_at, asset.c.taken_at)
    hidden = select(duplicate_member.c.asset_id).where(duplicate_member.c.is_representative.is_(False))
    rows = c.execute(
        select(
            asset.c.id, asset.c.content_hash, asset.c.rotation, when.label("at"), asset.c.id.in_(hidden).label("dup")
        )
        .where(
            asset.c.workspace_id == DEFAULT_WS,
            func.extract("month", when) == now.month,
            func.extract("year", when).between(oldest, now.year - 1),
        )
        .order_by(when, asset.c.id)
    ).all()
    from opagent_core.memories import _spread

    kept: dict[int, list[Any]] = {}
    counts: dict[int, int] = {}
    for r in rows:
        counts[r.at.year] = counts.get(r.at.year, 0) + 1
        if not r.dup:
            kept.setdefault(r.at.year, []).append(r)
    # picked evenly over the month: the first 24 by time were one morning of 424 (round 136, MEM-030)
    return {y: (_by_day(_spread(kept.get(y, []), MEMORY_MAX)), n) for y, n in counts.items()}


def _by_day(picked: list[Any]) -> list[Any]:
    """The picks a day at a time, round the days: listed by time, the 4 tiles shown were all one morning (MEM-033)."""
    days: dict[Any, list[Any]] = {}
    for r in picked:
        days.setdefault(r.at.date(), []).append(r)
    rounds = [list(d) for d in days.values()]
    out: list[Any] = []
    while any(rounds):
        for d in rounds:
            if d:
                out.append(d.pop(0))
    return out


FEEDBACK_REF = re.compile(r"(event:\d{4}-\d{2}-\d{2}|theme:\d{4}:[a-z_]+\.[a-z_]+|tag:[a-z_]+\.[a-z_]+)")


class FeedbackIn(BaseModel):
    kind: Literal["hide", "fewer"]
    ref: str = Field(max_length=120)


def _who(req: Request) -> str:
    st = _st(req)
    return str(req.state.opa_user["id"]) if getattr(st, "multi_user", False) else "local"


@router.get("/memories/feedback")
def memory_feedback_list(req: Request, lang: str = "zh-TW") -> dict[str, Any]:
    """The memories this person hid and the subjects they want fewer of (MEM-041), newest first; a theme's or a
    subject's name in ``lang``, for the list on 分類 (MEM-046)."""
    from opagent_db.schema import memory_feedback as mf

    from .search import tag_names

    with _st(req).engine.connect() as c:
        rows = c.execute(
            select(mf.c.id, mf.c.kind, mf.c.ref, mf.c.at)
            .where(mf.c.workspace_id == DEFAULT_WS, mf.c.user_id == _who(req))
            .order_by(mf.c.at.desc(), mf.c.id)
        ).all()
    key: dict[str, str] = {r.id: r.ref.split(":")[-1] for r in rows if r.ref.split(":")[0] in ("theme", "tag")}
    names = tag_names([k for k in key.values() if k], lang)
    return {
        "items": [
            {
                "id": r.id,
                "kind": r.kind,
                "ref": r.ref,
                "name": names.get(key[r.id], key[r.id]) if r.id in key else None,
                "at": r.at.isoformat() if r.at else None,
            }
            for r in rows
        ]
    }


@router.post("/memories/feedback")
def memory_feedback_add(body: FeedbackIn, req: Request) -> dict[str, Any]:
    """隱藏這個回憶 (an event or a theme) or 少一點「X」 (a subject), once each (ADR-0049 addendum 8, MEM-041)."""
    from opagent_db.schema import memory_feedback as mf
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    ok = FEEDBACK_REF.fullmatch(body.ref) and (body.ref.startswith("tag:") == (body.kind == "fewer"))
    if not ok:
        raise HTTPException(422, detail={"code": "VALIDATION", "field": "ref"})
    st = _st(req)
    who = _who(req)
    with st.engine.begin() as c:
        c.execute(
            pg_insert(mf)
            .values(id=uuid7(st.clock), workspace_id=DEFAULT_WS, user_id=who, kind=body.kind, ref=body.ref)
            .on_conflict_do_nothing(constraint="memory_feedback_once")
        )
        fid = c.execute(
            select(mf.c.id).where(
                mf.c.workspace_id == DEFAULT_WS, mf.c.user_id == who, mf.c.kind == body.kind, mf.c.ref == body.ref
            )
        ).scalar_one()
    return {"id": fid, "kind": body.kind, "ref": body.ref}


@router.delete("/memories/feedback/{fid}", status_code=204)
def memory_feedback_remove(fid: str, req: Request) -> Response:
    """還原: the memory or subject comes back (MEM-041)."""
    from opagent_db.schema import memory_feedback as mf

    with _st(req).engine.begin() as c:
        c.execute(mf.delete().where(mf.c.id == fid, mf.c.workspace_id == DEFAULT_WS, mf.c.user_id == _who(req)))
    return Response(status_code=204)


def _feedback(c: Any, req: Request) -> tuple[set[date], set[tuple[int, str]], set[str]]:
    """Hidden days, hidden (year, tag) themes and subjects wanted fewer, for this person."""
    from opagent_db.schema import memory_feedback as mf

    days: set[date] = set()
    hide: set[tuple[int, str]] = set()
    fewer: set[str] = set()
    for kind, ref in c.execute(
        select(mf.c.kind, mf.c.ref).where(mf.c.workspace_id == DEFAULT_WS, mf.c.user_id == _who(req))
    ).all():
        part = ref.split(":")
        if kind == "hide" and part[0] == "event":
            days.add(date.fromisoformat(part[1]))
        elif kind == "hide" and part[0] == "theme":
            hide.add((int(part[1]), part[2]))
        elif kind == "fewer" and part[0] == "tag":
            fewer.add(part[1])
    return days, hide, fewer


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
            return {"today": now.isoformat(), "scope": "days", "groups": []}
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
    if groups:
        return {"today": now.isoformat(), "scope": "days", "groups": groups}
    # none near the day in any year: the same month, said so; 那年今日 never showed on 18 November (round 135, MEM-026)
    with st.engine.connect() as c:
        months = _memory_months(c, oldest, now)
    for year in sorted(months, reverse=True):
        rows, n = months[year]
        if rows:
            groups.append(
                {
                    "year": year,
                    "years_ago": now.year - year,
                    "total": n,
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
    return {"today": now.isoformat(), "scope": "month", "groups": groups}


# a place or a thing (MEM-028, ADR-0049 addendum 7): the 6 themes were all about people; one slot a year is kept for
# these. Not a background (草地, 街道, 植物) and not worn or shown by a person (帽子, 口罩, 笑容)
NOT_A_PLACE_OR_THING = frozenset(
    {"scene.grass", "scene.street", "object.plant", "object.hat", "object.mask", "mood.smile"}
)


def place_or_thing(key: str) -> bool:
    return key.split(".", 1)[0] in {"scene", "object", "food", "mood"} and key not in NOT_A_PLACE_OR_THING


def _names_subject(caption: str | None, key: str | None) -> bool:
    """The caption names the subject in one of its Chinese forms: it leads the card (MEM-014, MEM-019)."""
    from .labels import tokenizer

    if not key:
        return False
    return any(f in (caption or "") for f in tokenizer()._forms_of(key) if f and not f.isascii())


def _image_vecs(c: Any, ids: list[str]) -> dict[str, list[float]]:
    """The active image space's vectors of these photos (none in fast mode without one), for covers (MEM-051)."""
    from opagent_db.schema import asset_embedding

    from .search import active_space

    space = active_space(c)
    if space is None or not ids:
        return {}
    rows = c.execute(
        select(asset_embedding.c.asset_id, asset_embedding.c.vec).where(
            asset_embedding.c.space_id == space.id, asset_embedding.c.asset_id.in_(ids)
        )
    ).all()
    import json

    # pgvector's text form ("[1,0.1]") when no vector type is registered on the connection
    return {r[0]: [float(x) for x in (json.loads(r[1]) if isinstance(r[1], str) else r[1])] for r in rows}


def _lead(ids: list[str] | tuple[str, ...], key: str | None, info: dict[str, Any], vecs: dict[str, list[float]]) -> str:
    """A memory's cover: the most typical photo of those whose caption names the subject (MEM-050/051)."""
    from opagent_core.memories import cover

    return cover(list(ids), vecs, prefer=lambda a: _names_subject(info[a].caption, key)) or ids[0]


def _today(req: Request) -> str:
    """The library's today, from the app's clock (invariant 11), for 「今年」「去年」 in memory titles (MEM-054)."""
    from zoneinfo import ZoneInfo

    from .labels import LOCAL_TZ

    return str(_st(req).clock.now().astimezone(ZoneInfo(LOCAL_TZ)).date().isoformat())


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
    # the events listed beside the themes, computed the same way: a theme mostly one of them repeats it (MEM-038)
    from zoneinfo import ZoneInfo

    from opagent_core.memories import events

    from .labels import LOCAL_TZ, tokenizer

    tz = ZoneInfo(LOCAL_TZ)
    with _st(req).engine.connect() as c:
        hidden_days, hide, fewer = _feedback(c, req)
    listed = events(
        [
            (r.id, r.at.astimezone(tz).date() if r.at.tzinfo else r.at.date(), list(r.tags or []))
            for r in rows
            if r.id not in dup
        ],
        skip=EVENT_SKIP,
        named_only=True,
        share=EVENT_SHARE,
        prefer=lambda a, key: _names_subject(info[a].caption, key),  # their covers as /memories/events gives them
    )

    # a hidden outing still counts as shown: hiding 「6/22 運動場」 brought 「2026 的運動場」 back (round 140, MEM-045)
    def hid(e: Any) -> bool:
        from datetime import timedelta

        last = e.last or e.day
        return any(e.day + timedelta(days=i) in hidden_days for i in range((last - e.day).days + 1))

    shown = [e for e in listed if not hid(e)]
    shown = sorted(shown, key=lambda e: e.key in fewer)[:EVENTS_MAX]
    listed = shown + [e for e in listed if hid(e)]
    # the count is what 「看全部」 finds, duplicates too; a theme mostly another's is not shown twice (MEM-013)
    found = themes(
        [(r.id, r.at.year, list(r.tags or [])) for r in rows],
        skip=tokenizer().not_themes(),
        max_overlap=THEME_OVERLAP,
        hidden=dup,
        place_or_thing=place_or_thing,
        claimed=[frozenset(e.all_ids) for e in listed],
        hide=hide,
        demote=fewer,
    )
    names = tag_names([t.key for t in found], lang)
    with _st(req).engine.connect() as c:
        vecs = _image_vecs(c, [a for e in shown for a in e.asset_ids] + [a for t in found for a in t.asset_ids])
    event_covers = {_lead(e.asset_ids, e.key, info, vecs) for e in shown if e.asset_ids}

    def cover_first(key: str, ids: tuple[str, ...]) -> list[str]:
        """The most typical photo whose caption names the subject leads (MEM-014, MEM-051), and not a listed event's
        cover when another will do: 「2025 的笑容」 had 「2025/11/10 · 玩具」's (MEM-044)."""
        if not ids:
            return []
        free = [a for a in ids if a not in event_covers] or list(ids)
        lead = _lead(free, key, info, vecs)
        return [lead, *(a for a in ids if a != lead)]

    return {
        "today": _today(req),
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
        ],
    }


EVENTS_MAX = 8
# an event is named by a subject on a quarter of its photos; under that its tags are spread flat, an import day:
# 2025/11/12 (126 photos, its subject on 39.7%) was dropped at 40% (audit round 100)
EVENT_SHARE = 0.25
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

    with _st(req).engine.connect() as c:
        hidden_days, _, fewer = _feedback(c, req)
    found = events(
        [(r.id, r.at.astimezone(tz).date() if r.at.tzinfo else r.at.date(), list(r.tags or [])) for r in rows],
        skip=EVENT_SKIP,
        named_only=True,  # an import day of unrelated photos is no outing (MEM-018)
        share=EVENT_SHARE,
        prefer=lambda a, key: names_subject(key, a),
        hidden_days=hidden_days,  # 隱藏這個回憶, 少一點「X」 (MEM-041)
        demote=fewer,
    )[:EVENTS_MAX]

    names = tag_names([e.key for e in found if e.key], lang)
    with _st(req).engine.connect() as c:
        vecs = _image_vecs(c, [a for e in found for a in e.asset_ids])

    def led(e: Any) -> list[str]:
        """The most typical photo naming the subject first, as the themes count it (MEM-051, MEM-044)."""
        if not e.asset_ids:
            return []
        lead = _lead(e.asset_ids, e.key, info, vecs)
        return [lead, *(a for a in e.asset_ids if a != lead)]

    return {
        "today": _today(req),
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
                    for a in led(e)
                ],
            }
            for e in found
        ],
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
