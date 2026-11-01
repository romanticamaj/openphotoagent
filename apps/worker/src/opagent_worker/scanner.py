"""Scanner: walk + hash + metadata (docs/02 §4.1, 09a §4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path

from opagent_core.path_template import DateSource
from opagent_fs.safepath import Sandbox


@dataclass(frozen=True)
class PhotoMeta:
    taken_at: datetime | None
    date_source: DateSource | None


def list_files(sb: Sandbox, alias: str, sub: str, include: list[str], exclude: list[str]) -> list[str]:
    return []


def content_hash(path: Path) -> str:
    return ""


def read_meta(path: Path, mtime: datetime, tz: tzinfo) -> PhotoMeta:
    return PhotoMeta(None, None)
