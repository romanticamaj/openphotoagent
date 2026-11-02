"""People decide on agent requests here (05 §3.6). Only the app's own UI can reach these routes: the desktop launch
token or browser session (``DesktopGuard``), plus the ``X-OPA-UI`` header a cross-site page cannot send without a
CORS preflight; a PAT is never a UI credential (MCP-APPR-002).
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from opagent_db.schema import approval, audit_log, job, mcp_token, output_action, output_plan
from pydantic import BaseModel
from sqlalchemy import func, insert, select, update

from .state import AppState

router = APIRouter()


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def ui_only(req: Request) -> None:
    auth = req.headers.get("authorization", "")
    if auth.startswith("Bearer opagent_pat_"):
        raise HTTPException(403, detail={"code": "UI_SESSION_REQUIRED"})
    if req.headers.get("x-opa-ui") != "1":
        raise HTTPException(403, detail={"code": "CSRF_CHECK_FAILED"})


def _aware(ts: Any) -> Any:
    return ts if ts is None or ts.tzinfo else ts.replace(tzinfo=UTC)


def _view(c: Any, st: AppState, a: Any) -> dict[str, Any]:
    from .mcp_tools import plan_hash

    status = a.status
    if status == "pending" and _aware(a.expires_at) <= st.clock.now():
        status = "expired"
    elif status == "pending" and plan_hash(c, a.plan_id) != a.plan_hash:
        status = "stale"
    counts = dict(
        c.execute(
            select(output_action.c.action, func.count())
            .where(output_action.c.plan_id == a.plan_id)
            .group_by(output_action.c.action)
        ).all()
    )
    tok = c.execute(select(mcp_token.c.id, mcp_token.c.name, mcp_token.c.seq).where(mcp_token.c.id == a.token_id)).first()
    return {
        "id": a.id,
        "plan_id": a.plan_id,
        "kind": a.kind,
        "status": status,
        "client": a.client_name,
        "grant": {"id": tok.id, "name": tok.name, "number": tok.seq} if tok else None,
        "requested_at": _aware(a.created_at).isoformat(),
        "expires_at": _aware(a.expires_at).isoformat(),
        "decided_at": _aware(a.decided_at).isoformat() if a.decided_at else None,
        "note": a.note,
        "counts": {k: int(v) for k, v in counts.items()},
    }


@router.get("/approvals")
def list_approvals(req: Request, status: str = "pending") -> list[dict[str, Any]]:
    st = _st(req)
    with st.engine.begin() as c:
        rows = c.execute(select(approval).order_by(approval.c.created_at.desc()).limit(200)).all()
        out = [_view(c, st, a) for a in rows]
    return [a for a in out if status == "all" or a["status"] == status]


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        a = c.execute(select(approval).where(approval.c.id == approval_id)).first()
        if a is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        return _view(c, st, a)


class DecisionIn(BaseModel):
    decision: str  # approve | reject
    move_confirmed: bool = False
    confirm_text: str | None = None


def decide(
    st: AppState,
    approval_id: str,
    decision: str,
    *,
    actor: str = "user",
    move_confirmed: bool = False,
    confirm_text: str | None = None,
) -> dict[str, Any]:
    from .mcp_tools import as_request, plan_hash
    from .routes_public import ApproveIn, approve, undo

    refuse: str | None = None
    with st.engine.begin() as c:  # the refusal is recorded first: an exception would roll it back
        a = c.execute(select(approval).where(approval.c.id == approval_id).with_for_update()).first()
        if a is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        if a.status != "pending":
            raise HTTPException(409, detail={"code": "APPROVAL_NOT_PENDING", "status": a.status})
        if _aware(a.expires_at) <= st.clock.now():
            c.execute(update(approval).where(approval.c.id == a.id).values(status="expired"))
            refuse = "APPROVAL_EXPIRED"
        elif plan_hash(c, a.plan_id) != a.plan_hash:  # the plan changed after the request (MCP-APPR-004)
            c.execute(update(approval).where(approval.c.id == a.id).values(status="stale"))
            refuse = "PLAN_STALE"
    if refuse:
        raise HTTPException(409, detail={"code": refuse})
    with st.engine.begin() as c:
        if decision == "reject":
            c.execute(
                update(approval).where(approval.c.id == a.id).values(status="rejected", decided_at=st.clock.now())
            )
            c.execute(insert(audit_log).values(actor=actor, action="approval.reject", target=a.plan_id, outcome="ok"))
            return {"status": "rejected"}
        if decision != "approve":
            raise HTTPException(422, detail={"code": "INVALID_DECISION"})
    # the plan's own approval rules apply unchanged: move needs a second confirmation, big moves a typed text
    if a.kind == "apply":
        approve(a.plan_id, as_request(st), ApproveIn(move_confirmed=move_confirmed, confirm_text=confirm_text))
    else:
        undo(a.plan_id, as_request(st))
    with st.engine.begin() as c:
        c.execute(update(approval).where(approval.c.id == a.id).values(status="approved", decided_at=st.clock.now()))
        c.execute(
            insert(audit_log).values(
                actor=actor,
                action=f"approval.{a.kind}",
                target=a.plan_id,
                outcome="ok",
                details={"approval_id": a.id, "client": a.client_name},
            )
        )
        jid = c.execute(
            select(job.c.id).join(output_plan, output_plan.c.job_id == job.c.id).where(output_plan.c.id == a.plan_id)
        ).scalar()
    return {"status": "approved", "job_id": jid}


@router.post("/approvals/{approval_id}/decision")
def post_decision(approval_id: str, body: DecisionIn, req: Request) -> dict[str, Any]:
    ui_only(req)
    return decide(
        _st(req),
        approval_id,
        body.decision,
        move_confirmed=body.move_confirmed,
        confirm_text=body.confirm_text,
    )
