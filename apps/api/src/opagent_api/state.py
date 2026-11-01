from __future__ import annotations

from dataclasses import dataclass

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

    def ensure_workspace(self) -> None:
        with self.engine.begin() as c:
            if c.execute(select(workspace.c.id).where(workspace.c.id == DEFAULT_WS)).first() is None:
                c.execute(insert(workspace).values(id=DEFAULT_WS, name="Default", settings={}))
