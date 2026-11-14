"""Desktop system endpoints (02 §10 desktop row, 08 §5.5): the uninstall prompt's trash summary."""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from opagent_db.schema import asset, output_action, output_plan
from pydantic import BaseModel
from sqlalchemy import func, select

from .state import AppState

router = APIRouter()

# the Worker's executor purges a plan's ``.opagent-trash/<plan>`` after this many days (OUT-UNDO-007)
TRASH_RETENTION_DAYS = 30
TRASHING_ACTIONS = ("move", "trash")


@router.get("/trash/summary")
def trash_summary(req: Request) -> dict[str, Any]:
    """Files the app moved into ``.opagent-trash/`` that are still there: applied, not undone, not yet purged.
    Built from the plan journal in the database — the API never reads photo folders (invariant 1)."""
    st: AppState = req.app.state.opa
    live = (
        (output_action.c.action.in_(TRASHING_ACTIONS))
        & (output_action.c.status == "done")
        & (output_plan.c.status.in_(("applied", "partially_applied")))
    )
    q = (
        select(
            func.count(),
            func.coalesce(func.sum(asset.c.size_bytes), 0),
            func.count(func.distinct(output_plan.c.id)),
            func.min(output_plan.c.approved_at),
            func.array_agg(func.distinct(output_action.c.src_alias)),
        )
        .select_from(output_action.join(output_plan, output_plan.c.id == output_action.c.plan_id))
        .join(asset, asset.c.id == output_action.c.asset_id)
        .where(live)
    )
    with st.engine.begin() as c:
        files, size, plans, oldest, roots = c.execute(q).one()
    expiry = oldest + timedelta(days=TRASH_RETENTION_DAYS) if oldest is not None else None
    return {
        "files": int(files),
        "bytes": int(size),
        "plans": int(plans),
        "roots": sorted(r for r in (roots or []) if r is not None),
        "earliest_expiry": expiry.astimezone(UTC).isoformat() if expiry else None,
        "retention_days": TRASH_RETENTION_DAYS,
    }


def _backups(st: AppState) -> Any:
    from opagent_db.embedded import find_pg_bin
    from opagent_db.lifecycle import Backups

    if st.backups_dir is None:
        raise HTTPException(404, detail={"code": "BACKUPS_UNAVAILABLE"})  # Docker / dev: the operator backs up PG
    return Backups(st.backups_dir, st.pg_bin or find_pg_bin(), st.clock)


def _view(b: Any) -> dict[str, Any]:
    return {"name": b.path.name, "kind": b.kind, "at": b.at, "size_bytes": b.path.stat().st_size}


@router.get("/system/backups")
def list_backups(req: Request) -> dict[str, Any]:
    st: AppState = req.app.state.opa
    return {"items": [_view(b) for b in _backups(st).list()]}


@router.post("/system/backup", status_code=201)
def backup_now(req: Request) -> dict[str, Any]:
    st: AppState = req.app.state.opa
    return _view(_backups(st).manual(st.dsn))


class RestoreIn(BaseModel):
    name: str
    confirm: bool = False


@router.post("/system/restore")
def restore(body: RestoreIn, req: Request) -> JSONResponse:
    """Replace the database with a listed backup. Everything written since is lost, so the UI confirms first."""
    st: AppState = req.app.state.opa
    bk = _backups(st)
    item = bk.find(body.name)
    if item is None:
        raise HTTPException(404, detail={"code": "BACKUP_NOT_FOUND"})
    if not body.confirm:
        return JSONResponse({"code": "RESTORE_CONFIRM_REQUIRED", "backup": _view(item)}, status_code=409)
    st.engine.dispose()  # no pooled connection holds locks while objects are dropped and recreated
    bk.restore(st.dsn, item)
    st.engine.dispose()
    return JSONResponse({"restored": _view(item)})
