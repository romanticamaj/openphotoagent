"""Roles and permission strings (02 §1.3; MU-RBAC-001..003).

Roles are fixed sets of permission strings (no custom roles before v1). Every state-changing ``/api/v1`` route is
mapped to exactly one permission here; a route that is missing fails MU-RBAC-001 and is refused at runtime
(fail closed). Safe methods (GET/HEAD) need ``ws:read`` unless listed.

Org roles (``org_owner``, ``org_admin``) are stored with ``workspace_id = "*"`` and apply in every workspace.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

ORG_WIDE = "*"

VIEWER = frozenset({"ws:read", "face:read"})
REVIEWER = VIEWER | {"review:write"}
EDITOR = REVIEWER | {"job:create", "assistant:use", "face:write"}
WS_ADMIN = EDITOR | {"output:apply", "source:write", "model:manage", "face:admin", "assistant:admin", "mcp:manage"}
ORG_ADMIN = frozenset({"ws:read", "member:manage", "workspace:manage", "worker:manage"})
ALL = WS_ADMIN | ORG_ADMIN | {"org:delete"}

ROLES: dict[str, frozenset[str]] = {
    "org_owner": ALL,
    "org_admin": ORG_ADMIN,
    "ws_admin": WS_ADMIN,
    "ws_editor": EDITOR,
    "ws_reviewer": REVIEWER,
    "ws_viewer": VIEWER,
}
ORG_ROLES = {"org_owner", "org_admin"}

# MCP scope → the permission its holder's role must have (05 §3.2; the effective scope is token ∩ role).
SCOPE_PERMISSION = {
    "photos:read": "ws:read",
    "photos:media": "ws:read",
    "photos:describe": "job:create",
    "labels:write": "review:write",
    "collections:write": "job:create",
    "jobs:run": "job:create",
    "organize:propose": "job:create",
    "organize:apply": "output:apply",
    "faces:read": "face:read",
    "faces:media": "face:read",
}

R, J, RV, OUT, SRC, MDL = "ws:read", "job:create", "review:write", "output:apply", "source:write", "model:manage"
FR, FW, FA, AU, AA, MM = "face:read", "face:write", "face:admin", "assistant:use", "assistant:admin", "mcp:manage"
WM, MEM, WSM = "worker:manage", "member:manage", "workspace:manage"

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/approvals/{approval_id}/decision"): OUT,
    ("POST", "/api/v1/ask"): AU,
    ("POST", "/api/v1/ask/turns/{turn_id}/cancel"): AU,
    ("PATCH", "/api/v1/assets/{asset_id}/labels"): RV,
    ("PATCH", "/api/v1/assistant-settings"): AA,
    ("POST", "/api/v1/auth/handoff"): R,
    ("POST", "/api/v1/auth/logout"): R,
    ("POST", "/api/v1/benchmarks"): J,
    ("POST", "/api/v1/benchmarks/{bid}/apply"): SRC,
    ("DELETE", "/api/v1/conversations/{cid}"): AU,
    ("POST", "/api/v1/duplicates/cleanup-plan"): J,
    ("POST", "/api/v1/duplicates/recluster"): J,
    ("POST", "/api/v1/estimates"): R,
    ("POST", "/api/v1/export/label-studio"): R,
    ("POST", "/api/v1/faces/cluster"): FW,
    ("POST", "/api/v1/faces/disable"): FA,
    ("POST", "/api/v1/faces/enable"): FA,
    ("POST", "/api/v1/faces/find"): FR,
    ("GET", "/api/v1/faces/review"): FR,
    ("GET", "/api/v1/people"): FR,
    ("GET", "/api/v1/people/{pid}"): FR,
    ("GET", "/api/v1/people/{pid}/candidates"): FR,
    ("POST", "/api/v1/faces/maintenance"): FA,
    ("POST", "/api/v1/faces/plan-by-person"): FW,
    ("POST", "/api/v1/faces/prompt/dismiss"): FA,
    ("POST", "/api/v1/faces/purge"): FA,
    ("POST", "/api/v1/faces/purge/verify"): FA,
    ("POST", "/api/v1/faces/query-image"): FR,
    ("POST", "/api/v1/faces/space-migrations"): FA,
    ("POST", "/api/v1/faces/space-migrations/{mid}/complete"): FA,
    ("POST", "/api/v1/faces/{fid}/confirm"): FW,
    ("POST", "/api/v1/faces/{fid}/name"): FW,  # VIEW-032
    ("POST", "/api/v1/faces/{fid}/reject"): FW,
    ("POST", "/api/v1/golden-sets"): J,
    ("POST", "/api/v1/import/label-studio"): RV,
    ("PUT", "/api/v1/integrations/immich"): SRC,
    ("POST", "/api/v1/integrations/immich/plans"): J,
    ("POST", "/api/v1/integrations/immich/plans/{pid}/apply"): OUT,
    ("POST", "/api/v1/integrations/immich/plans/{pid}/undo"): OUT,
    ("POST", "/api/v1/jobs"): J,
    ("POST", "/api/v1/jobs/{job_id}/cancel"): J,
    ("POST", "/api/v1/jobs/{job_id}/pause"): J,
    ("POST", "/api/v1/jobs/{job_id}/reapply"): J,
    ("POST", "/api/v1/jobs/{job_id}/resume"): J,
    ("POST", "/api/v1/jobs/{job_id}/reviews/skip-rest"): RV,
    ("DELETE", "/api/v1/licenses/{model_id}/accept"): MDL,
    ("POST", "/api/v1/licenses/{model_id}/accept"): MDL,
    ("POST", "/api/v1/mcp/clients/{client}/connect"): MM,
    ("POST", "/api/v1/mcp/clients/{client}/install"): MM,
    ("PUT", "/api/v1/mcp/settings"): MM,
    ("POST", "/api/v1/mcp/tokens"): R,  # scopes are capped by the creator's role
    ("DELETE", "/api/v1/mcp/tokens/{token_id}"): R,  # own tokens; others' need mcp:manage
    ("POST", "/api/v1/members"): MEM,
    ("DELETE", "/api/v1/members/{user_id}"): MEM,
    ("POST", "/api/v1/models/import"): MDL,
    ("POST", "/api/v1/models/import/scan"): MDL,
    ("POST", "/api/v1/models/{key}/remote-code/confirm"): MDL,
    ("POST", "/api/v1/map/basemap/download"): MDL,  # ADR-0037
    ("POST", "/api/v1/onboarding/install"): MDL,
    ("POST", "/api/v1/operations/pause-all"): J,
    ("POST", "/api/v1/operations/reorder"): J,
    ("POST", "/api/v1/operations/resume-all"): J,
    ("POST", "/api/v1/operations/sweep"): J,
    ("POST", "/api/v1/operations/{op_id}/{action}"): J,
    ("POST", "/api/v1/output-plans/{plan_id}/approve"): OUT,
    ("POST", "/api/v1/output-plans/{plan_id}/undo"): OUT,
    ("POST", "/api/v1/people/merges/{token}/undo"): FW,
    ("POST", "/api/v1/people/{pid}/flags"): FW,
    ("POST", "/api/v1/people/{pid}/ignore"): FW,
    ("POST", "/api/v1/people/{pid}/merge"): FW,
    ("POST", "/api/v1/people/merge-suggestions/dismiss"): FW,
    ("POST", "/api/v1/people/{pid}/name"): FW,
    ("POST", "/api/v1/people/{pid}/review"): FW,
    ("POST", "/api/v1/people/{pid}/review/undo"): FW,  # FACE-FIND-016
    ("POST", "/api/v1/people/{pid}/split"): FW,
    ("POST", "/api/v1/preflight"): R,
    ("POST", "/api/v1/presets/import"): SRC,
    ("POST", "/api/v1/previews"): J,
    ("POST", "/api/v1/previews/{preview_id}/rerender"): J,
    ("POST", "/api/v1/previews/{preview_id}/try"): J,
    ("POST", "/api/v1/reviews/{review_id}"): RV,
    ("POST", "/api/v1/search"): R,
    ("POST", "/api/v1/search-index/reindex"): SRC,
    ("POST", "/api/v1/search/by-image"): R,
    ("POST", "/api/v1/search/caption-index"): SRC,
    ("POST", "/api/v1/search/caption-index/switch"): SRC,
    ("POST", "/api/v1/selections"): R,
    ("POST", "/api/v1/selections/{sid}/labels"): RV,
    ("POST", "/api/v1/selections/{sid}/organize"): J,
    ("POST", "/api/v1/sources"): SRC,
    ("POST", "/api/v1/sources/{source_id}/scan"): J,
    ("POST", "/api/v1/sources/{source_id}/analyze"): J,
    ("PUT", "/api/v1/sources/{source_id}/watch"): SRC,
    ("POST", "/api/v1/system/backup"): WSM,
    ("POST", "/api/v1/system/fixes/{rest}"): WSM,
    ("POST", "/api/v1/system/restore"): WSM,
    ("POST", "/api/v1/workers/enrollment-codes"): WM,
    ("DELETE", "/api/v1/workers/{worker_id}"): WM,
    ("DELETE", "/api/v1/workers/{worker_id}/installations/{inst_id}"): WM,
    ("POST", "/api/v1/workers/{worker_id}/installs"): WM,
    ("POST", "/api/v1/workspaces"): WSM,
    ("DELETE", "/api/v1/oauth/grants/{grant_id}"): R,  # own grants; others' need mcp:manage
    ("PUT", "/api/v1/workspace/egress-policy"): SRC,
}
SAFE = {"GET", "HEAD", "OPTIONS"}


def permissions(roles: dict[str, str], ws: str) -> frozenset[str]:
    """What a member can do in ``ws``: the workspace role plus any org-wide role."""
    out: set[str] = set()
    for scope in (ws, ORG_WIDE):
        role = roles.get(scope)
        if role:
            out |= ROLES.get(role, frozenset())
    return frozenset(out)


def can(role: str, perm: str) -> bool:
    return perm in ROLES.get(role, frozenset())


def capped_scopes(scopes: Iterable[str], perms: frozenset[str]) -> list[str]:
    """Scopes the holder's role allows; ``organize:apply`` is never issued to an MCP token."""
    return [s for s in scopes if s != "organize:apply" and SCOPE_PERMISSION.get(s) in perms]


class RouteTable:
    """Matches a concrete path to its route template (literal segments win over parameters)."""

    def __init__(self, paths: Iterable[str]) -> None:
        compiled = []
        for p in paths:
            rx = re.sub(r"\{[^}:]+:path\}", ".+", p)
            rx = re.sub(r"\{[^}]+\}", "[^/]+", rx)
            compiled.append((p.count("{"), p, re.compile("^" + rx + "$")))
        self.routes = sorted(compiled, key=lambda t: (t[0], -len(t[1])))

    def template(self, path: str) -> str | None:
        for _, tpl, rx in self.routes:
            if rx.match(path):
                return tpl
        return None


def required(method: str, template: str | None) -> str | None:
    """The permission a request needs; ``None`` = unmapped (refused)."""
    if template is None:
        return "ws:read" if method in SAFE else None
    if method in SAFE:
        return ROUTES.get((method, template), "ws:read")
    return ROUTES.get((method, template))
