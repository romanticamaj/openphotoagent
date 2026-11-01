"""Output executor: plan -> approve -> journal -> execute (docs/02 §6.2, invariant 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .fsops import FsOps
from .journal import Journal
from .safepath import Sandbox


@dataclass(frozen=True)
class Action:
    action_id: str
    op: Literal["copy"]
    src_alias: str
    src_rel: str
    dst_alias: str
    dst_rel: str
    src_hash: str


@dataclass
class ActionResult:
    action_id: str
    status: Literal["done", "skipped", "failed", "undone", "conflict"]
    dst_rel: str | None = None
    error: str | None = None


class Executor:
    def __init__(self, sandbox: Sandbox, fs: FsOps, journal: Journal) -> None:
        self.sb = sandbox
        self.fs = fs
        self.journal = journal

    def apply(self, plan_id: str, actions: list[Action]) -> list[ActionResult]:
        return []

    def undo(self, plan_id: str) -> list[ActionResult]:
        return []
