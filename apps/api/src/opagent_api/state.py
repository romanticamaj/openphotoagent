from __future__ import annotations

from dataclasses import dataclass
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
    registry_dir: Path | None = None
    user_models_dir: Path | None = None
    thumbs_dir: Path | None = None

    def registry(self) -> Any:
        """Built-in catalog + pins + the user's YAML (re-read so a new custom model shows up, INF-REG-003)."""
        from opagent_registry.loader import load_registry

        users = [self.user_models_dir] if self.user_models_dir else []
        return load_registry(self.registry_dir, user_dirs=users)

    def ensure_workspace(self) -> None:
        with self.engine.begin() as c:
            if c.execute(select(workspace.c.id).where(workspace.c.id == DEFAULT_WS)).first() is None:
                c.execute(insert(workspace).values(id=DEFAULT_WS, name="Default", settings={}))
