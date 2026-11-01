"""SafePath: the single entry point for every file operation (docs/02 §6.1, invariant 2)."""

from __future__ import annotations

import os
import re
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


_DRIVE = re.compile(r"^[A-Za-z]:")


class Sandbox:
    """Resolves ``alias + relative path`` against the worker's allowlisted roots."""

    def __init__(self, roots: list[RootConfig]) -> None:
        self.roots = {r.alias: r for r in roots}
        self._real = {r.alias: Path(os.path.realpath(r.path)) for r in roots}

    def resolve(self, alias: str, rel: str) -> SafePath:
        root = self._real[alias]
        if rel.startswith("/") or _DRIVE.match(rel):
            raise PathViolation("absolute", alias, rel)
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." for p in rel.split("/")):
            raise PathViolation("dotdot", alias, rel)
        real = root.joinpath(*parts)
        if os.path.commonpath([root, real]) != str(root):
            raise PathViolation("escape", alias, rel)
        return SafePath(alias, "/".join(parts), real)

    def open_write(self, alias: str, rel: str) -> SafePath:
        if self.roots[alias].mode != "rw":
            raise PathViolation("read_only_root", alias, rel)
        return self.resolve(alias, rel)
