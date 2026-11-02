"""Organize / preview job flow on the control plane (02 §4.1). Pure planning; no file access (invariant 1).

scan pages -> asset upsert -> (by-date) plan | (model presets) label items -> results -> decide -> plan
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from opagent_core.config_hash import config_hash
from opagent_core.decide import Policy
from opagent_core.ids import uuid7
from opagent_core.planner import PlanItem, assign_collisions, plan, summarize
from opagent_core.preview import stratified_sample
from opagent_core.rules import load_preset
from opagent_core.state_machine import JobStatus
from opagent_db.schema import (
    asset,
    asset_location,
    job,
    job_item,
    output_action,
    output_plan,
    source,
    worker,
    worker_task,
)
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import queue as q
from .db import offset_minutes, set_job_status, to_aware
from .operations import ensure_operation, set_op_status
from .state import DEFAULT_WS

PRESETS_DIR = Path(__file__).resolve().parents[4] / "presets" / "builtin"
ACTIVE_JOB = ("queued", "running", "paused", "planning", "applying", "cancelling")


@lru_cache(maxsize=8)
def _builtin(key: str) -> Any:
    return load_preset((PRESETS_DIR / f"{key}.yaml").read_text(encoding="utf-8"))


def preset(key: str, categories: list[dict[str, Any]] | None = None) -> Any:
    """Built-in preset, or ``custom``: the by-content pipeline and output rules with the user's categories plus the
    automatic 「其他」 (03 §3.3)."""
    if key != "custom":
        return _builtin(key)
    from dataclasses import replace

    base = _builtin("by-content-12")
    cats = [
        {
            "key": c["key"],
            "zh-TW": c.get("name") or c["key"],
            "en": c.get("name") or c["key"],
            "description": {"zh-TW": c.get("description", ""), "en": c.get("description", "")},
            "examples": list(c.get("examples") or []),
        }
        for c in categories or []
        if c.get("key") and c["key"] != "other"
    ]
    other = next(c for c in base.categories if c["key"] == "other")
    return replace(base, key="custom", categories=[*cats, other])


def task_config_for(
    preset_key: str, categories: list[dict[str, Any]] | None = None, c: Any = None
) -> dict[str, Any] | None:
    p = preset(preset_key, categories)
    if not p.task_configs:
        return None
    tc = dict(p.task_configs[0])
    tc["categories"] = [x["key"] for x in p.categories]
    # built-in rules (e.g. screenshots → documents) only apply when their target category exists
    tc["rules"] = [r for r in tc.get("rules") or [] if r.get("then") in tc["categories"]]
    # English names/descriptions feed the SigLIP2 prompts and the VLM enum (03 §3.3); display names stay zh/en.
    # Example photos become prototypes; their locations are part of the config hash (invariant 7).
    tc["classes"] = [
        {
            "id": x["key"],
            "name": x.get("en", x["key"]),
            "description": (x.get("description") or {}).get("en", ""),
            **({"examples": _example_refs(c, x["examples"])} if x.get("examples") and c is not None else {}),
        }
        for x in p.categories
    ]
    return tc


def _example_refs(c: Any, asset_ids: list[str]) -> list[dict[str, str]]:
    rows = c.execute(
        select(asset_location.c.asset_id, source.c.root_alias, asset_location.c.rel_path, asset.c.content_hash)
        .join(source, source.c.id == asset_location.c.source_id)
        .join(asset, asset.c.id == asset_location.c.asset_id)
        .where(asset_location.c.asset_id.in_(asset_ids), asset_location.c.missing.is_(False))
        .order_by(asset_location.c.asset_id, asset_location.c.rel_path)
    ).all()
    seen: dict[str, dict[str, str]] = {}
    for r in rows:
        seen.setdefault(r.asset_id, {"root_alias": r.root_alias, "rel": r.rel_path, "content_hash": r.content_hash})
    return [seen[a] for a in asset_ids if a in seen]


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
                exif=e.get("exif") or {},
                local_at=(taken or mtime).replace(tzinfo=None),
            )
            .on_conflict_do_update(
                index_elements=["workspace_id", "content_hash"],
                set_={
                    "size_bytes": e["size"],
                    "local_at": func.coalesce(asset.c.local_at, (taken or mtime).replace(tzinfo=None)),
                },
            )
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
    tc = task_config_for(params["preset"], params.get("categories"), c)
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


def _plan_sidecars(
    st: Any,
    c: Any,
    pid: str,
    planned: list[Any],
    rows: list[Any],
    labels: dict[str, dict[str, Any]] | None,
    root_alias: str,
    target: dict[str, Any],
    sidecars: set[str],
) -> None:
    """``<stem>.opagent.json`` next to every organized photo and one ``opagent-export.csv`` per batch (02 §6, ORG-006).
    Written through the journal like any output, so undo removes them (invariant 3)."""
    import posixpath

    from opagent_db.schema import result

    by_row = {r.asset_id: r for r in rows}
    now = st.clock.now().isoformat()
    seq = c.execute(select(func.count()).select_from(output_action).where(output_action.c.plan_id == pid)).scalar_one()
    acts, records = [], []
    for a in planned:
        r = by_row.get(a.asset_id)
        lab = (labels or {}).get(a.asset_id) or {}
        res = c.execute(
            select(result.c.caption, result.c.tags, result.c.model_fingerprint)
            .where(result.c.asset_id == a.asset_id)
            .order_by(result.c.created_at.desc())
            .limit(1)
        ).first()
        rec = {
            "schema": "opagent.sidecar/1",
            "file": posixpath.basename(a.dst_rel),
            "source": a.src_rel,
            "content_hash": a.src_hash,
            "taken_at": (to_aware(r.taken_at, r.taken_at_offset_min) or r.taken_at).isoformat()  # the photo's clock
            if r is not None and r.taken_at
            else None,
            "date_source": a.date_source,
            "category": lab.get("category_key"),
            "decision": lab.get("decision"),
            "decided_by": lab.get("source"),
            "confidence": round(float(lab["confidence"]), 4) if lab.get("confidence") is not None else None,
            "caption": res.caption if res else None,
            "tags": [str(t) for t in (res.tags or [])] if res else [],
            "model_fingerprint": res.model_fingerprint if res else None,
            "generated_at": now,
        }
        records.append(rec)
        if "json" in sidecars:
            stem = posixpath.splitext(a.dst_rel)[0]
            acts.append(("sidecar_json", a, f"{stem}.opagent.json", rec))
    if "csv" in sidecars:
        from opagent_core.export import to_csv

        cols = ["file", "source", "category", "decision", "confidence", "caption", "tags", "taken_at", "generated_at"]
        # captions/tags are untrusted text: to_csv quotes and defuses spreadsheet formulas (OUT-EXE-021, invariant 6)
        text = to_csv([{**rec, "tags": ";".join(rec["tags"])} for rec in records], cols)
        sub = target.get("sub_path", "").strip("/")
        acts.append(
            (
                "sidecar_csv",
                planned[0],
                posixpath.join(sub, "opagent-export.csv") if sub else "opagent-export.csv",
                {"text": text},
            )
        )
    c.execute(
        insert(output_action),
        [
            {
                "id": uuid7(st.clock),
                "plan_id": pid,
                "seq": seq + i,
                "asset_id": a.asset_id,
                "action": op,
                "src_alias": root_alias,
                "src_rel": a.src_rel,
                "dst_alias": target["root_alias"],
                "dst_rel": dst,
                "src_hash": a.src_hash,
                "payload": payload,
            }
            for i, (op, a, dst, payload) in enumerate(acts)
        ],
    )


def maybe_finish(st: Any, c: Any, job_id: str) -> None:
    j = c.execute(select(job).where(job.c.id == job_id)).first()
    if j is None or j.status != "running" or j.kind not in ("organize", "preview", "label"):
        return
    open_items = c.execute(
        select(func.count())
        .select_from(job_item)
        .where(job_item.c.job_id == job_id, job_item.c.status.in_(("pending", "leased", "running")))
    ).scalar_one()
    if open_items:
        return
    if j.kind == "label":  # labels only (run_labeling_job): the legal "label, no output" path, never a plan (CORE-SM-005)
        for s in (JobStatus.planning, JobStatus.awaiting_approval):
            set_job_status(c, job_id, s, clock=st.clock)
        set_job_status(c, job_id, JobStatus.completed, clock=st.clock, finished_at=st.clock.now())
        return
    if j.kind == "preview":
        if j.params.get("preview_ready"):
            return
        c.execute(update(job).where(job.c.id == job_id).values(params={**j.params, "preview_ready": True}))
        _queue_estimate(st, c, j)
        return
    src_id = j.params["source_id"]
    build_plan(st, c, j, _source_assets(c, src_id))


ESTIMATE_CAP = 5000  # embed at most this many photos for the library forecast; the rest is extrapolated


def _queue_estimate(st: Any, c: Any, j: Any) -> None:
    """ORG-007: library-wide distribution from the embedding fast path (no VLM), run on the worker."""
    tc = (j.params or {}).get("task_config") or {}
    if tc.get("strategy") != "cascade":
        return
    s = c.execute(select(source).where(source.c.id == j.params["source_id"])).first()
    rows = list(_source_assets(c, s.id))
    step = max(1, len(rows) // ESTIMATE_CAP + (1 if len(rows) % ESTIMATE_CAP else 0)) if len(rows) > ESTIMATE_CAP else 1
    sample = rows[::step][:ESTIMATE_CAP]
    c.execute(
        insert(worker_task).values(
            id=uuid7(st.clock),
            kind="estimate",
            job_id=j.id,
            worker_id=s.worker_id,
            payload={
                "job_id": j.id,
                "root_alias": s.root_alias,
                "total": len(rows),
                "task_config": tc,
                "items": [{"rel": r.rel_path, "content_hash": r.content_hash} for r in sample],
            },
        )
    )


def labels_for(c: Any, job_id: str, policy: Policy | None = None) -> dict[str, dict[str, Any]]:
    """asset_id -> final label for this job (human label > model decision; see labels.py)."""
    from .labels import labels_for as _labels_for

    return _labels_for(c, job_id, policy)


def rebuild_draft_plan(st: Any, c: Any, job_id: str) -> str | None:
    """Review changes re-render the draft plan (no inference). Approved/applied plans are never touched."""
    j = c.execute(select(job).where(job.c.id == job_id)).first()
    if j is None or j.status != "awaiting_approval" or not j.plan_id:
        return None
    p = c.execute(select(output_plan.c.status).where(output_plan.c.id == j.plan_id)).scalar()
    if p != "draft":
        return None
    c.execute(output_action.delete().where(output_action.c.plan_id == j.plan_id))
    c.execute(update(output_plan).where(output_plan.c.id == j.plan_id).values(status="superseded"))
    build_plan(st, c, j, _source_assets(c, j.params["source_id"]))
    return str(c.execute(select(job.c.plan_id).where(job.c.id == job_id)).scalar())


def plan_items(c: Any, j: Any, rows: list[Any], labels: dict[str, dict[str, Any]] | None = None) -> list[PlanItem]:
    names = {cat["key"]: cat["zh-TW"] for cat in preset(j.params["preset"], j.params.get("categories")).categories}
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
    labels = labels_for(c, j.id) if task_config_for(params["preset"], params.get("categories")) else None
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
                    "action": params.get("action") or "copy",
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
    sidecars = set((params.get("advanced") or {}).get("sidecars") or [])
    if planned and sidecars:
        _plan_sidecars(st, c, pid, planned, rows, labels, root_alias, target, sidecars)
    plan_op = f"{pid}:plan"
    ensure_operation(c, st.clock, plan_op, "output.plan", {"kind": "plan", "id": pid})
    set_op_status(c, st.clock, plan_op, "running")
    set_op_status(c, st.clock, plan_op, "succeeded")
    cnt = q.counters(c, j.id)
    counters = {**cnt, "total": cnt["total"] or len(planned), "planned": len(planned)}
    if j.status == "awaiting_approval":  # re-render after review changes: same status, new draft plan
        c.execute(update(job).where(job.c.id == j.id).values(plan_id=pid, counters=counters))
        return None
    set_job_status(c, j.id, JobStatus.awaiting_approval, clock=st.clock, plan_id=pid, counters=counters)
    return None


def active_job_for_source(c: Any, source_id: str) -> Any:
    return c.execute(
        select(job.c.id)
        .where(job.c.params["source_id"].astext == source_id, job.c.status.in_(ACTIVE_JOB), job.c.kind == "organize")
        .order_by(job.c.created_at)
    ).scalar()


def config_hash_for(tc: dict[str, Any]) -> str:
    return config_hash(tc)


def _p50(rng: list[float] | None, default: float) -> float:
    if not rng or len(rng) != 2:
        return default
    return float((rng[0] * rng[1]) ** 0.5)  # geometric middle of the [p10, p90] prior


def finish_estimate(st: Any, c: Any, job_id: str, res: dict[str, Any]) -> None:
    """Scale the embedded sample to the whole library (largest remainder keeps the sum exact) and attach a
    p50 duration from throughput priors (06 §5.9 basis=prior until perf_observation exists)."""
    from opagent_registry.loader import load_registry

    j = c.execute(select(job).where(job.c.id == job_id)).first()
    if j is None:
        return
    total, n = int(res.get("total", 0)), max(1, int(res.get("n_embedded", 0)))
    counts: dict[str, int] = res.get("counts") or {}
    exact = {k: v * total / n for k, v in counts.items()}
    dist = {k: int(v) for k, v in exact.items()}
    for k in sorted(exact, key=lambda k: -(exact[k] - dist[k]))[: total - sum(dist.values())]:
        dist[k] += 1
    routed = round(int(res.get("routed_to_vlm", 0)) * total / n)
    reg = load_registry()
    emb = reg.models.get("siglip2-so400m-384")
    vlm = reg.models.get("qwen3-vl-8b-instruct")
    embed_rate = _p50(
        ((emb.spec.variants[0].perf_prior or {}).get("items_per_s") or {}).get("embed") if emb else None, 40.0
    )
    vlm_rate = _p50(
        ((vlm.spec.variants[0].perf_prior or {}).get("items_per_s") or {}).get("fused") if vlm else None, 1.0
    )
    p50 = total / embed_rate + routed / vlm_rate
    est = {
        "total": total,
        "distribution": dist,
        "routed_to_vlm": routed,
        "basis": {"method": "embedding_fast_path", "sample": int(j.params.get("k", 10)), "embedded": n},
        "time": {"p50_s": round(p50, 1), "basis": "prior"},
    }
    c.execute(update(job).where(job.c.id == job_id).values(params={**j.params, "estimate": est}))


def start_label_job(
    st: Any, asset_ids: list[str], *, only_unlabeled: bool, priority: str, origin: dict[str, Any]
) -> dict[str, Any]:
    """A labelling-only job over given photos (05 §3.3 run_labeling_job): results and reviews, never a plan or a
    file action — it ends ``completed`` once every item is labelled (dry-run by construction)."""
    import posixpath

    from opagent_db.schema import result

    from .operations import ensure_operation

    with st.engine.begin() as c:
        tc = task_config_for("by-content-12", None, c)
        assert tc is not None
        rows = c.execute(
            select(asset_location.c.asset_id, asset_location.c.rel_path, source.c.root_alias, source.c.sub_path,
                   source.c.worker_id)
            .join(source, source.c.id == asset_location.c.source_id)
            .where(asset_location.c.asset_id.in_(asset_ids or ["-"]), asset_location.c.missing.is_(False))
            .order_by(asset_location.c.asset_id, asset_location.c.rel_path)
        ).all()
        first: dict[str, Any] = {}
        for r in rows:
            first.setdefault(r.asset_id, r)
        if only_unlabeled:
            done = {a for (a,) in c.execute(select(result.c.asset_id).where(result.c.asset_id.in_(list(first))))}
            first = {a: r for a, r in first.items() if a not in done}
        picked = [first[a] for a in asset_ids if a in first]
        wid = picked[0].worker_id if picked else None
        fp = worker_fingerprint(c, wid, tc["task_type"])
        jid = uuid7(st.clock)
        c.execute(
            insert(job).values(
                id=jid,
                workspace_id="default",
                kind="label",
                status=JobStatus.queued.value,
                mode="dry_run",
                params={**origin, "preset": "by-content-12", "priority": priority, "worker_id": wid,
                        "task_config": tc, "fingerprint": fp},
            )
        )
        ensure_operation(c, st.clock, jid, "job.label", {"kind": "job", "id": jid})
        set_job_status(c, jid, JobStatus.running, clock=st.clock)
        n = q.create_label_items(
            c,
            st.clock,
            jid,
            [(r.asset_id, r.root_alias, posixpath.join(r.sub_path.strip("/"), r.rel_path) if r.sub_path.strip("/")
              else r.rel_path) for r in picked],
            tc,
            fp,
        )
        maybe_finish(st, c, jid)
        status = c.execute(select(job.c.status).where(job.c.id == jid)).scalar_one()
    return {"job_id": jid, "status": status, "estimated_items": n["total"], "cached_items": n["cached"],
            "estimate": {"items": n["total"] - n["cached"]}}
