"""Low-risk auto-approval settings (05 §3.6 item 4, §5): which planned actions may be applied without a person
clicking approve (MCP agents, automations). Only non-destructive actions qualify; ``trash``, ``move``,
``hardlink`` and anything overwriting always need a person (TRASH-007). ``organize:apply`` itself is never given to
MCP tokens (CLAUDE.md §3) — this governs the plans a person has opted into.
"""

from __future__ import annotations

from typing import Any

ELIGIBLE = {"copy", "symlink", "sidecar_xmp", "sidecar_json", "sidecar_csv"}
NEVER = {"move", "trash", "hardlink"}
MAX_ACTIONS = 500


class AutoApproveError(ValueError):
    def __init__(self, code: str, actions: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.actions = actions or []


def validate_auto_approve(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return the normalised setting or raise: destructive or unknown actions and overwrites are refused."""
    actions = [str(a) for a in cfg.get("actions") or []]
    destructive = sorted({a for a in actions if a in NEVER})
    if destructive:
        raise AutoApproveError("AUTO_APPROVE_DESTRUCTIVE", destructive)
    unknown = sorted({a for a in actions if a not in ELIGIBLE})
    if unknown:
        raise AutoApproveError("AUTO_APPROVE_UNKNOWN_ACTION", unknown)
    if cfg.get("overwrite"):
        raise AutoApproveError("AUTO_APPROVE_DESTRUCTIVE", ["overwrite"])
    return {
        "actions": sorted(set(actions)),
        "max_actions": max(0, min(int(cfg.get("max_actions") or MAX_ACTIONS), MAX_ACTIONS)),
        "targets": [str(t) for t in cfg.get("targets") or []],
    }


def eligible(cfg: dict[str, Any], actions: list[str], *, overwrites: int = 0, target: str | None = None) -> bool:
    """Whether a plan with these action kinds may skip the person (MCP-APPR-007): every action is in the
    configured non-destructive set, nothing overwrites, the plan fits ``max_actions`` and, when targets are
    configured, the plan writes into one of them."""
    if not actions or overwrites or len(actions) > cfg["max_actions"]:
        return False
    if any(a in NEVER or a not in cfg["actions"] for a in actions):
        return False
    return not cfg["targets"] or target in cfg["targets"]
