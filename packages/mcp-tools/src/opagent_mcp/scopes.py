"""Token scopes → tools (05 §3.2.3; ADR-0009).

A tool is callable when the token holds its scope, the workspace switch it needs is on, and the running server
ships it. ``tools/list`` shows exactly the callable tools in name order; anything else is unknown to the caller
(JSON-RPC -32601) and logged with the reason.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .tools import SERVER_VERSION, TOOLS, Tool

ALL_SCOPES = (
    "photos:read",
    "photos:media",
    "photos:describe",
    "labels:write",
    "collections:write",
    "jobs:run",
    "organize:propose",
    "organize:apply",
    "faces:read",
    "faces:media",
)
DEFAULT_SCOPES = ("photos:read",)  # what the connect wizard issues (MCP-SCOPE-002, D-1)
READONLY_SCOPES = frozenset({"photos:read", "photos:media", "faces:read", "faces:media"})


@dataclass(frozen=True)
class Settings:
    """Workspace MCP settings (05 §3.5–§3.6, 07 §5)."""

    allow_apply_requests: bool = False
    faces_enabled: bool = False
    media_policy: str = "thumb_256"  # none | thumb_256 | thumb_512 | thumb_1024
    face_media_policy: str = "none"
    exif_free_text: bool = False


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def allowed(
    tool: Tool, scopes: Iterable[str], settings: Settings, version: str = SERVER_VERSION
) -> tuple[bool, str | None]:
    """(callable, reason it is not): ``not_available`` / ``insufficient_scope`` / ``setting_disabled``."""
    if _ver(tool.available_from) > _ver(version):
        return False, "not_available"
    if tool.scope not in set(scopes):
        return False, "insufficient_scope"
    if tool.setting and not getattr(settings, tool.setting):
        return False, "setting_disabled"
    return True, None


def visible(scopes: Iterable[str], settings: Settings, version: str = SERVER_VERSION) -> list[Tool]:
    s = frozenset(scopes)
    return sorted((t for t in TOOLS if allowed(t, s, settings, version)[0]), key=lambda t: t.name)


def normalise(scopes: Iterable[str]) -> list[str]:
    """Known scopes in canonical order; ``organize:apply`` is accepted here but never issued to MCP tokens."""
    s = set(scopes)
    unknown = s - set(ALL_SCOPES)
    if unknown:
        raise ValueError(f"unknown scopes: {sorted(unknown)}")
    return [x for x in ALL_SCOPES if x in s]
