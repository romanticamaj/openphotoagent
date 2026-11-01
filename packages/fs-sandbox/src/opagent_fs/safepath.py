"""SafePath: the single entry point for every file operation (docs/02 §6.1, invariant 2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Mode = Literal["ro", "rw"]


class PathViolation(Exception):  # noqa: N818 - name from 09a FS-*
    code = "PATH_VIOLATION"

    def __init__(self, reason: str, alias: str = "", rel: str = "") -> None:
        super().__init__(f"{reason}: {alias}:{rel}")
        self.reason = reason
        self.alias = alias
        self.rel = rel


@dataclass(frozen=True)
class RootConfig:
    alias: str
    path: str
    mode: Mode = "ro"


@dataclass(frozen=True)
class SafePath:
    alias: str
    rel: str
    real: Path


class Sandbox:
    def __init__(self, roots: list[RootConfig]) -> None:
        self.roots = {r.alias: r for r in roots}

    def resolve(self, alias: str, rel: str) -> SafePath:
        return SafePath(alias, "", Path("/"))

    def open_write(self, alias: str, rel: str) -> SafePath:
        return self.resolve(alias, rel)
