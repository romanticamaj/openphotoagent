"""job_item queue (02 §4.3): SKIP LOCKED leases, heartbeats, idempotent results, retry backoff, cache lookup."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from opagent_core.config_hash import config_hash
from opagent_core.ids import uuid7
from opagent_db.schema import job, job_item, metric, result
from sqlalchemy import and_, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

LEASE_S = 120
BACKOFF_S = (10, 60, 300)  # 02 §4.3: transient retries 10 s, 60 s, 5 min
TERMINAL = ("done", "cached", "failed", "skipped", "cancelled", "review")


def bump_metric(conn: Any, name: str, n: int = 1) -> None:
    conn.execute(
        pg_insert(metric)
        .values(name=name, value=n)
        .on_conflict_do_update(index_elements=["name"], set_={"value": metric.c.value + n})
    )


def create_label_items(
    conn: Any,
    clock: Any,
    job_id: str,
    assets: list[tuple[str, str, str]],
    task_config: dict[str, Any],
    fingerprint: str,
    task: str = "classify",
    lane: str = "normal",
) -> dict[str, int]:
    """One item per asset; items whose (asset, config_hash, fingerprint) result exists are ``cached`` (Q-014)."""
    h = config_hash(task_config)
    ids = [a for a, _, _ in assets]
    have = {
        r[0]: r[1]
        for r in conn.execute(
            select(result.c.asset_id, result.c.id).where(
                result.c.asset_id.in_(ids), result.c.task_config_hash == h, result.c.model_fingerprint == fingerprint
            )
        )
    }
    rows = []
    for i, (aid, alias, rel) in enumerate(assets):
        cached = aid in have
        rows.append(
            {
                "id": uuid7(clock),
                "job_id": job_id,
                "asset_id": aid,
                "root_alias": alias,
                "rel": rel,
                "task": task,
                "lane": lane,
                "seq": i,
                "status": "cached" if cached else "pending",
                "result_id": have.get(aid),
            }
        )
    if rows:
        conn.execute(job_item.insert(), rows)
    return {"total": len(rows), "cached": sum(r["status"] == "cached" for r in rows)}


def lease(
    conn: Any,
    clock: Any,
    worker_id: str,
    max_items: int,
    loaded: list[str] | None = None,
    lanes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Lease up to ``max_items`` pending items of running jobs. Interactive lane first, then model affinity
    (items whose task matches an already-loaded model, Q-017), then FIFO."""
    now = clock.now()
    rows = conn.execute(
        text("""
        WITH c AS (
          SELECT ji.id FROM job_item ji JOIN job j ON j.id = ji.job_id
          WHERE ji.status = 'pending' AND j.status = 'running'
            AND (ji.next_attempt_at IS NULL OR ji.next_attempt_at <= :now)
            AND (CAST(:lanes AS text[]) IS NULL OR ji.lane = ANY(CAST(:lanes AS text[])))
          ORDER BY (ji.lane = 'interactive') DESC, (ji.task = ANY(CAST(:loaded AS text[]))) DESC, j.created_at,
                   ji.seq, ji.id
          LIMIT :n FOR UPDATE OF ji SKIP LOCKED)
        UPDATE job_item SET status = 'leased', lease_owner = :w, lease_expires_at = :exp, attempts = attempts + 1
        FROM c WHERE job_item.id = c.id
        RETURNING job_item.id, job_item.job_id, job_item.asset_id, job_item.root_alias, job_item.rel,
                  job_item.task, job_item.lane, job_item.attempts
    """),
        {
            "now": now,
            "w": worker_id,
            "exp": now + timedelta(seconds=LEASE_S),
            "n": max_items,
            "loaded": loaded or [],
            "lanes": lanes,
        },
    ).all()
    out = []
    for r in rows:
        j = conn.execute(select(job.c.params).where(job.c.id == r.job_id)).scalar_one()
        out.append(
            {
                "id": r.id,
                "job_id": r.job_id,
                "asset_id": r.asset_id,
                "root_alias": r.root_alias,
                "rel": r.rel,
                "task": r.task,
                "lane": r.lane,
                "attempt": r.attempts,
                "task_config": j.get("task_config"),
                "content_hash": None,
            }
        )
    return sorted(out, key=lambda x: (x["lane"] != "interactive", x["id"]))


def heartbeat(conn: Any, clock: Any, worker_id: str, lease_ids: list[str]) -> int:
    res = conn.execute(
        update(job_item)
        .where(
            job_item.c.id.in_(lease_ids),
            job_item.c.lease_owner == worker_id,
            job_item.c.status.in_(("leased", "running")),
        )
        .values(lease_expires_at=clock.now() + timedelta(seconds=LEASE_S))
    )
    return res.rowcount or 0


def sweep(conn: Any, clock: Any) -> int:
    """Expired leases go back to pending (attempts already counts the lost lease). Worker tasks (scan/apply/undo)
    keep their payload, so a re-leased scan resumes from its cursor (SCAN-019)."""
    from opagent_db.schema import worker_task

    conn.execute(
        update(worker_task)
        .where(worker_task.c.status == "leased", worker_task.c.lease_expires_at < clock.now())
        .values(status="pending", worker_id=None)
    )
    res = conn.execute(
        update(job_item)
        .where(job_item.c.status.in_(("leased", "running")), job_item.c.lease_expires_at < clock.now())
        .values(status="pending", lease_owner=None, lease_expires_at=None)
    )
    n = res.rowcount or 0
    if n:
        bump_metric(conn, "opagent_lease_expired_total", n)
    return n


def report(conn: Any, clock: Any, worker_id: str, items: list[dict[str, Any]]) -> dict[str, int]:
    accepted = 0
    for it in items:
        ji = conn.execute(select(job_item).where(job_item.c.id == it["job_item_id"]).with_for_update()).first()
        if ji is None:
            continue
        params = conn.execute(select(job.c.params).where(job.c.id == ji.job_id)).scalar_one()
        cfg_hash = config_hash(params.get("task_config") or {})
        if it["status"] == "ok":
            out = it.get("output") or {}
            fp = it.get("model_fingerprint") or params.get("fingerprint") or ""
            rid = conn.execute(
                pg_insert(result)
                .values(
                    id=uuid7(clock),
                    asset_id=ji.asset_id,
                    task_config_hash=cfg_hash,
                    model_fingerprint=fp,
                    job_item_id=ji.id,
                    task_type=(params.get("task_config") or {}).get("task_type", ji.task),
                    category=out.get("category"),
                    confidence=out.get("confidence"),
                    caption=out.get("caption"),
                    tags=out.get("tags"),
                    raw={"provenance": it.get("provenance") or {}, "output": out},
                )
                .on_conflict_do_nothing(index_elements=["asset_id", "task_config_hash", "model_fingerprint"])
                .returning(result.c.id)
            ).scalar()
            if rid is None:  # duplicate / late report: keep the first result (Q-004, Q-005)
                rid = conn.execute(
                    select(result.c.id).where(
                        and_(
                            result.c.asset_id == ji.asset_id,
                            result.c.task_config_hash == cfg_hash,
                            result.c.model_fingerprint == fp,
                        )
                    )
                ).scalar_one()
            if ji.status not in ("done", "cached"):
                conn.execute(
                    update(job_item)
                    .where(job_item.c.id == ji.id)
                    .values(status="done", result_id=rid, lease_owner=None, lease_expires_at=None, error_code=None)
                )
            accepted += 1
            continue
        if ji.status in TERMINAL or ji.lease_owner != worker_id or it.get("attempt") != ji.attempts:
            continue  # stale failure from a lease that no longer exists
        if it.get("retryable") and ji.attempts < ji.max_attempts:
            delay = BACKOFF_S[min(ji.attempts - 1, len(BACKOFF_S) - 1)]
            conn.execute(
                update(job_item)
                .where(job_item.c.id == ji.id)
                .values(
                    status="pending",
                    lease_owner=None,
                    lease_expires_at=None,
                    error_code=it.get("error_code"),
                    next_attempt_at=clock.now() + timedelta(seconds=delay),
                )
            )
        else:
            conn.execute(
                update(job_item)
                .where(job_item.c.id == ji.id)
                .values(status="failed", lease_owner=None, lease_expires_at=None, error_code=it.get("error_code"))
            )
        accepted += 1
    for jid in {
        conn.execute(select(job_item.c.job_id).where(job_item.c.id == it["job_item_id"])).scalar() for it in items
    }:
        if jid:
            finish_cancel_if_idle(conn, clock, jid)
            from .organize import maybe_finish

            maybe_finish(_St(clock), conn, jid)
    return {"accepted": accepted}


def release(conn: Any, worker_id: str, lease_ids: list[str]) -> int:
    """Give leases back without counting an attempt (immediate pause, Q-010)."""
    res = conn.execute(
        update(job_item)
        .where(
            job_item.c.id.in_(lease_ids),
            job_item.c.lease_owner == worker_id,
            job_item.c.status.in_(("leased", "running")),
        )
        .values(status="pending", lease_owner=None, lease_expires_at=None, attempts=job_item.c.attempts - 1)
    )
    return res.rowcount or 0


def counters(conn: Any, job_id: str) -> dict[str, int]:
    rows = conn.execute(
        select(job_item.c.status, func.count()).where(job_item.c.job_id == job_id).group_by(job_item.c.status)
    ).all()
    out = {k: 0 for k in ("pending", "leased", "running", "done", "cached", "failed", "review", "skipped", "cancelled")}
    for s, n in rows:
        out[s] = n
    out["total"] = sum(n for _, n in rows)
    return out


class _St:
    def __init__(self, clock: Any) -> None:
        self.clock = clock


def finish_cancel_if_idle(conn: Any, clock: Any, job_id: str) -> None:
    """cancelling -> cancelled once the last lease is resolved (CORE-SM-003)."""
    from opagent_core.state_machine import JobStatus

    from .db import set_job_status

    st = conn.execute(select(job.c.status).where(job.c.id == job_id)).scalar()
    if st != "cancelling":
        return
    active = conn.execute(
        select(func.count())
        .select_from(job_item)
        .where(job_item.c.job_id == job_id, job_item.c.status.in_(("leased", "running")))
    ).scalar_one()
    if active == 0:
        set_job_status(conn, job_id, JobStatus.cancelled, clock=clock)


def abort_list(conn: Any, worker_id: str, lease_ids: list[str]) -> list[str]:
    """Leases the worker should stop working on: no longer owned by it, or their job is paused/cancelled."""
    if not lease_ids:
        return []
    rows = conn.execute(
        select(job_item.c.id, job_item.c.lease_owner, job_item.c.status, job.c.status.label("js"))
        .join(job, job.c.id == job_item.c.job_id)
        .where(job_item.c.id.in_(lease_ids))
    ).all()
    return [
        r.id
        for r in rows
        if r.lease_owner != worker_id
        or r.status not in ("leased", "running")
        or r.js in ("paused", "cancelling", "cancelled")
    ]
