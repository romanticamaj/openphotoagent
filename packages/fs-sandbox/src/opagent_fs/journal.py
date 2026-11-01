"""Append-only executor journal in local SQLite/WAL (docs/02 §3.2 journal_entry, §6.2)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


class Journal:
    def __init__(self, path: Path, on_event: Callable[[str], None] | None = None) -> None:
        self.path = path
        self.on_event = on_event

    def intent(self, plan_id: str, action_id: str, op: str, args: dict[str, Any], pre_state: dict[str, Any]) -> int:
        return 0

    def done(self, seq: int, post_state: dict[str, Any]) -> None:
        pass

    def entries(self, plan_id: str) -> list[dict[str, Any]]:
        return []
