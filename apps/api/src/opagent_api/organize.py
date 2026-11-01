"""Organize / preview job flow on the control plane (02 §4.1). Pure planning; no file access (invariant 1).

scan pages -> asset upsert -> (by-date) plan | (model presets) label items -> results -> decide -> plan
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from opagent_core.config_hash import config_hash
from opagent_core.decide import Policy, VlmResult, decide
from opagent_core.ids import uuid7
from opagent_core.planner import PlanItem, assign_collisions, plan, summarize
from opagent_core.preview import stratified_sample
from opagent_core.rules import load_preset
from opagent_core.state_machine import JobStatus
from opagent_db.schema import asset, asset_location, job, job_item, output_action, output_plan, result, source, worker
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import queue as q
from .db import offset_minutes, set_job_status, to_aware
from .operations import ensure_operation, set_op_status
from .state import DEFAULT_WS

PRESETS_DIR = Path(__file__).resolve().parents[4] / "presets" / "builtin"
ACTIVE_JOB = ("queued", "running", "paused", "planning", "applying", "cancelling")


@lru_cache(maxsize=8)
def preset(key: str) -> Any:
    return load_preset((PRESETS_DIR / f"{key}.yaml").read_text(encoding="utf-8"))


def task_config_for(preset_key: str) -> dict[str, Any] | None:
    p = preset(preset_key)
    if not p.task_configs:
        return None
    tc = dict(p.task_configs[0])
    tc["categories"] = [c["key"] for c in p.categories]
    return tc


def upsert_entries(st: Any, c: Any, src_id: str, entries: list[dict[str, Any]]) -> None:
    now = st.clock.now()
    for e in entries:
        taken = datetime.fromisoformat(e["taken_at"]) if e.get("taken_at") else None
        mtime = datetime.fromisoformat(e["mtime"])
        aid = c.execute(
            pg_insert(asset)
            .values(
                id=uuid7(st.clock),
                workspace_id=DEFAULT_WS,
                content_hash=e["content_hash"],
                size_bytes=e["size"],
                taken_at=taken,
                taken_at_offset_min=offset_minutes(taken) if taken else None,
                date_source=e.get("date_source"),
                width=e.get("width"),
                height=e.get("height"),
                thumbhash=e.get("thumbhash"),
                phash=e.get("phash"),
                dhash=e.get("dhash"),
            )
            .on_conflict_do_update(index_elements=["workspace_id", "content_hash"], set_={"size_bytes": e["size"]})
            .returning(asset.c.id)
        ).scalar_one()
        c.execute(
            pg_insert(asset_location)
            .values(
                id=uuid7(st.clock),
                asset_id=aid,
                source_id=src_id,
                rel_path=e["rel"],
                mtime=mtime,
                mtime_offset_min=offset_minutes(mtime),
                size=e["size"],
                last_seen_at=now,
            )
            .on_conflict_do_update(
                index_elements=["source_id", "rel_path"],
                set_={"asset_id": aid, "mtime": mtime, "size": e["size"], "missing": False, "last_seen_at": now},
            )
        )


def finish_scan(st: Any, c: Any, t: Any) -> None:
    """Locations not seen by this scan are marked missing (moved / deleted files), then the job continues."""
    src_id = t.payload["source_id"]
    started = datetime.fromisoformat(t.payload["scan_started_at"])
    c.execute(
        update(asset_location)
        .where(asset_location.c.source_id == src_id, asset_location.c.last_seen_at < started)
        .values(missing=True)
    )
    c.execute(update(source).where(source.c.id == src_id).values(last_scan_at=st.clock.now()))
    j = c.execute(select(job).where(job.c.id == t.job_id)).first()
    if j is not None:
        after_scan(st, c, j, t)


def _source_assets(c: Any, src_id: str) -> Any:
    return c.execute(
        select(
            asset_location.c.asset_id,
            asset_location.c.rel_path,
            asset_location.c.mtime,
            asset_location.c.mtime_offset_min,
            asset.c.content_hash,
            asset.c.taken_at,
            asset.c.taken_at_offset_min,
            asset.c.date_source,
        )
        .join(asset, asset.c.id == asset_location.c.asset_id)
        .where(asset_location.c.source_id == src_id, asset_location.c.missing.is_(False))
        .order_by(asset_location.c.rel_path)
    ).all()


def worker_fingerprint(c: Any, worker_id: str | None, task: str) -> str:
    caps = c.execute(select(worker.c.capabilities).where(worker.c.id == worker_id)).scalar() or {}
    return str((caps.get("models") or {}).get(task) or "unknown")


def after_scan(st: Any, c: Any, j: Any, t: Any) -> None:
    params = j.params
    tc = task_config_for(params["preset"])
    rows = _source_assets(c, t.payload["source_id"])
    if tc is None:
        return build_plan(st, c, j, rows)
    fp = worker_fingerprint(c, t.worker_id, tc["task_type"])
    if j.kind == "preview":
        sample = stratified_sample(
            [
                {
                    "id": r.asset_id,
                    "folder": r.rel_path.rsplit("/", 1)[0] if "/" in r.rel_path else "",
                    "month": (r.taken_at or r.mtime).strftime("%Y-%m"),
                }
                for r in rows
            ],
            k=int(params.get("k", 10)),
            seed=int(params.get("seed", 42)),
        )
        keep = set(sample)
        rows = [r for r in rows if r.asset_id in keep]
    c.execute(update(job).where(job.c.id == j.id).values(params={**params, "task_config": tc, "fingerprint": fp}))
    q.create_label_items(
        c,
        st.clock,
        j.id,
        [(r.asset_id, t.payload["root_alias"], r.rel_path) for r in rows],
        tc,
        fp,
        lane="interactive" if j.kind == "preview" else "normal",
    )
    maybe_finish(st, c, j.id)


def maybe_finish(st: Any, c: Any, job_id: str) -> None:
    j = c.execute(select(job).where(job.c.id == job_id)).first()
    if j is None or j.status != "running" or j.kind not in ("organize", "preview"):
        return
    open_items = c.execute(
        select(func.count())
        .select_from(job_item)
        .where(job_item.c.job_id == job_id, job_item.c.status.in_(("pending", "leased", "running")))
    ).scalar_one()
    if open_items:
        return
    if j.kind == "preview":
        c.execute(update(job).where(job.c.id == job_id).values(params={**j.params, "preview_ready": True}))
        return
    src_id = j.params["source_id"]
    build_plan(st, c, j, _source_assets(c, src_id))


def labels_for(c: Any, job_id: str, policy: Policy | None = None) -> dict[str, dict[str, Any]]:
    """asset_id -> final label from this job's results (ADR-0001 decide; 'other' when not accepted)."""
    rows = c.execute(
        select(job_item.c.asset_id, result.c.category, result.c.confidence, result.c.raw)
        .join(result, result.c.id == job_item.c.result_id)
        .where(job_item.c.job_id == job_id)
    ).all()
    out = {}
    for r in rows:
        o = decide(vlm=VlmResult(r.category or "other", r.confidence or 0.0), policy=policy or Policy())
        out[r.asset_id] = {
            "category_key": o.category if o.decision == "accepted" else "other",
            "decision": o.decision,
            "confidence": o.confidence,
        }
    return out


def plan_items(c: Any, j: Any, rows: list[Any], labels: dict[str, dict[str, Any]] | None = None) -> list[PlanItem]:
    names = {cat["key"]: cat["zh-TW"] for cat in preset(j.params["preset"]).categories}
    items = []
    for r in rows:
        lab = (labels or {}).get(r.asset_id)
        key = lab["category_key"] if lab else None
        items.append(
            PlanItem(
                r.asset_id,
                r.rel_path,
                r.content_hash,
                to_aware(r.taken_at, r.taken_at_offset_min),
                r.date_source,
                to_aware(r.mtime, r.mtime_offset_min),  # type: ignore[arg-type]
                category=names.get(key) if key else None,
                category_key=key,
            )
        )
    return items


def build_plan(st: Any, c: Any, j: Any, rows: list[Any]) -> None:
    params = j.params
    target = params["target"]
    if j.status == "running":
        set_job_status(c, j.id, JobStatus.planning, clock=st.clock)
    labels = labels_for(c, j.id) if task_config_for(params["preset"]) else None
    planned = assign_collisions(
        plan(plan_items(c, j, rows, labels), params["preset"], target.get("sub_path", "").strip("/")),
        case_insensitive=True,
    )
    pid = uuid7(st.clock)
    root_alias = c.execute(select(source.c.root_alias).where(source.c.id == params["source_id"])).scalar_one()
    c.execute(insert(output_plan).values(id=pid, job_id=j.id, status="draft", summary=summarize(planned)))
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
                    "src_alias": root_alias,
                    "src_rel": a.src_rel,
                    "dst_alias": target["root_alias"],
                    "dst_rel": a.dst_rel,
                    "src_hash": a.src_hash,
                    "date_source": a.date_source,
                    "conflict": a.conflict,
                }
                for i, a in enumerate(planned)
            ],
        )
    plan_op = f"{pid}:plan"
    ensure_operation(c, st.clock, plan_op, "output.plan", {"kind": "plan", "id": pid})
    set_op_status(c, st.clock, plan_op, "running")
    set_op_status(c, st.clock, plan_op, "succeeded")
    cnt = q.counters(c, j.id)
    set_job_status(
        c,
        j.id,
        JobStatus.awaiting_approval,
        clock=st.clock,
        plan_id=pid,
        counters={**cnt, "total": cnt["total"] or len(planned), "planned": len(planned)},
    )
    return None


def active_job_for_source(c: Any, source_id: str) -> Any:
    return c.execute(
        select(job.c.id)
        .where(job.c.params["source_id"].astext == source_id, job.c.status.in_(ACTIVE_JOB), job.c.kind == "organize")
        .order_by(job.c.created_at)
    ).scalar()


def config_hash_for(tc: dict[str, Any]) -> str:
    return config_hash(tc)
