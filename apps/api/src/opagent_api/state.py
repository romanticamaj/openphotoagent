from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opagent_core.clock import Clock
from opagent_db.schema import workspace
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

DEFAULT_WS = "default"


@dataclass
class AppState:
    engine: Engine
    clock: Clock
    launch_token: str | None = None
    lan_password_hash: str | None = None  # set only when "allow LAN access" is on (08 §1.3)
    backups_dir: Path | None = None  # desktop: Settings → Data backups (08 §2.3)
    pg_bin: Path | None = None
    dsn: str = ""
    blobs: dict[str, bytes] = field(default_factory=dict)  # uploaded query images, memory only (SRCH-IMG-002)
    handoffs: dict[str, Any] = field(default_factory=dict)  # one-time browser handoff codes → expiry
    login_failures: dict[str, list[Any]] = field(default_factory=dict)  # LAN login rate limit per client
    sessions: set[str] = field(default_factory=set)  # browser sessions opened from the tray
    registry_dir: Path | None = None
    user_models_dir: Path | None = None
    thumbs_dir: Path | None = None
    home: Path | None = None  # data folder: the local MCP credential fallback lives here (MCP-PAT-001)
    public_url: str = "http://127.0.0.1:47800"  # web/Docker approval links (05 §3.6)
    handle_key: bytes = field(default_factory=lambda: __import__("secrets").token_bytes(32))  # MCP cursors

    def workspace_settings(self, c: Any) -> dict[str, Any]:
        return dict(c.execute(select(workspace.c.settings).where(workspace.c.id == DEFAULT_WS)).scalar() or {})

    def registry(self) -> Any:
        """Built-in catalog + pins + the user's YAML (re-read so a new custom model shows up, INF-REG-003)."""
        from opagent_registry.loader import load_registry

        users = [self.user_models_dir] if self.user_models_dir else []
        return load_registry(self.registry_dir, user_dirs=users)

    def ensure_workspace(self) -> None:
        with self.engine.begin() as c:
            if c.execute(select(workspace.c.id).where(workspace.c.id == DEFAULT_WS)).first() is None:
                c.execute(insert(workspace).values(id=DEFAULT_WS, name="Default", settings={}))
