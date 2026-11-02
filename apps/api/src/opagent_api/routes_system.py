"""Desktop system endpoints (02 §10 desktop row, 08 §5.5): the uninstall prompt's trash summary."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request
from opagent_db.schema import asset, output_action, output_plan
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
        "earliest_expiry": expiry.isoformat() if expiry else None,
        "retention_days": TRASH_RETENTION_DAYS,
    }
