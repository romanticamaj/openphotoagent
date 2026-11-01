"""path_template rendering (docs/02 §3.2, 09a §1.2). Pure: no I/O.

Syntax: ``{var}`` and ``{var:<strftime>}``; UI aliases ``{date:YYYY}`` etc. compile to
``{taken_at:%Y}``. Missing ``taken_at`` falls back to ``mtime`` and reports
``date_source="mtime"`` (CORE-TPL-004).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

DateSource = Literal["exif_offset", "exif_gps_tz", "exif_local_assumed", "mtime"]
_TOKEN = re.compile(r"\{([a-z_]+)(?::([^}]*))?\}")
_DATE_ALIASES = {"YYYY": "%Y", "YYYY-MM": "%Y-%m", "MM": "%m", "DD": "%d", "YYYY-MM-DD": "%Y-%m-%d"}


@dataclass(frozen=True)
class RenderValues:
    stem: str
    ext: str
    taken_at: datetime | None = None
    taken_at_source: DateSource = "exif_offset"
    mtime: datetime | None = None
    category: str | None = None
    category_key: str | None = None


@dataclass(frozen=True)
class RenderResult:
    rel: str
    date_source: DateSource | None


@dataclass(frozen=True)
class _Part:
    literal: str | None = None
    var: str | None = None
    fmt: str | None = None


@dataclass(frozen=True)
class Template:
    source: str
    parts: tuple[_Part, ...]

    @property
    def uses_date(self) -> bool:
        return any(p.var == "taken_at" for p in self.parts)


def compile_template(template: str) -> Template:
    parts: list[_Part] = []
    pos = 0
    for m in _TOKEN.finditer(template):
        if m.start() > pos:
            parts.append(_Part(literal=template[pos : m.start()]))
        var, fmt = m.group(1), m.group(2)
        if var == "date":
            var, fmt = "taken_at", _DATE_ALIASES.get(fmt or "YYYY", fmt)
        parts.append(_Part(var=var, fmt=fmt))
        pos = m.end()
    if pos < len(template):
        parts.append(_Part(literal=template[pos:]))
    return Template(template, tuple(parts))


def sanitize_segment(value: str) -> str:
    return value


def render(
    tpl: Template,
    values: RenderValues,
    fallback_values: dict[str, str] | None = None,
    lang: str = "zh-TW",
) -> RenderResult:
    when, source = values.taken_at, values.taken_at_source
    if when is None:
        when, source = values.mtime, "mtime"
    out: list[str] = []
    for p in tpl.parts:
        if p.literal is not None:
            out.append(p.literal)
        elif p.var == "taken_at":
            assert when is not None, "either taken_at or mtime is required"
            out.append(when.strftime(p.fmt or "%Y-%m-%d"))
        else:
            out.append(str(getattr(values, p.var or "", "") or ""))
    return RenderResult(rel="".join(out), date_source=source if tpl.uses_date else None)
