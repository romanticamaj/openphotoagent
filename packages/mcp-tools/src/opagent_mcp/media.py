"""Which image size, if any, may go to an MCP client (05 §3.5; 07 §5.4). The strictest of the token scope, the
workspace ``mcp_media_policy`` (or ``mcp_face_media_policy`` for faces) and the egress policy wins; the reason is
always reported so the agent can tell the user why it sees no image."""

from __future__ import annotations

POLICY_MAX = {"none": 0, "thumb_256": 256, "thumb_512": 512, "thumb_1024": 1024}
EGRESS_MAX = {"full": 1024, "thumbs": 256, "edge-thumbs": 256, "results-only": 0}
IMAGE_BUDGET = 200 * 1024  # bytes of image per response (MCP-MEDIA-004)
STRUCTURED_MAX = 256 * 1024


def effective_size(requested: int, *, has_scope: bool, policy: str, egress: str = "full") -> tuple[int, str | None]:
    """(longest edge to send, withheld reason). ``0`` with no reason means none was asked for."""
    if requested <= 0:
        return 0, None
    if not has_scope:
        return 0, "scope_missing"
    if EGRESS_MAX.get(egress, 0) == 0:
        return 0, "privacy_results_only"
    cap = min(POLICY_MAX.get(policy, 0), EGRESS_MAX[egress])
    if cap == 0:
        return 0, "policy"
    return min(requested, cap), None
