"""path_template rendering (docs/02 §3.2, 09a §1.2). Pure: no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

DateSource = Literal["exif_offset", "exif_gps_tz", "exif_local_assumed", "mtime"]


@dataclass(frozen=True)
class RenderValues:
    stem: str
    ext: str
    taken_at: datetime | None = None
    mtime: datetime | None = None
    category: str | None = None
    category_key: str | None = None


@dataclass(frozen=True)
class RenderResult:
    rel: str
    date_source: DateSource | None


@dataclass(frozen=True)
class Template:
    source: str


def compile_template(template: str) -> Template:
    return Template(template)


def render(tpl: Template, values: RenderValues) -> RenderResult:
    return RenderResult(rel="", date_source=None)
