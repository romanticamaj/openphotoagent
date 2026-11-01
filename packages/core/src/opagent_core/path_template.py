"""path_template rendering (docs/02 §3.2, 09a §1.2). Pure: no I/O.

Syntax: ``{var}`` and ``{var:<strftime>}``; UI aliases ``{date:YYYY}`` etc. compile to
``{taken_at:%Y}``. Missing ``taken_at`` falls back to ``mtime`` and reports
``date_source="mtime"`` (CORE-TPL-004). Every variable value is sanitized into a single
safe path segment (CORE-TPL-006..015, CORE-TPL-024).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

DateSource = Literal["exif_offset", "exif_gps_tz", "exif_local_assumed", "mtime"]
_TOKEN = re.compile(r"\{([a-z_]+)(?::([^}]*))?\}")
_DATE_ALIASES = {"YYYY": "%Y", "YYYY-MM": "%Y-%m", "MM": "%m", "DD": "%d", "YYYY-MM-DD": "%Y-%m-%d"}
MAX_SEGMENT_BYTES = 255

# characters that must never appear in a segment: path separators, Windows-illegal, and slash look-alikes
_REPLACE = set('/\\:*?"<>|') | {"／", "∕", "⁄", "＼"}
# invisible / direction-changing characters (ZWJ U+200D is kept: it is part of emoji sequences)
_REMOVE = {chr(c) for c in range(0x20)} | {"\x7f", "​", "‎", "‏", "﻿"} | {
    chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))
}
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_UNKNOWN = {"zh-TW": "_未知", "en": "_unknown"}


@dataclass(frozen=True)
class RenderValues:
    stem: str
    ext: str
    taken_at: datetime | None = None
    taken_at_source: DateSource = "exif_offset"
    mtime: datetime | None = None
    category: str | None = None
    category_key: str | None = None
    country: str | None = None
    tags: tuple[tuple[str, float], ...] = ()
    tag_display: dict[str, str] | None = None
    admin1: str | None = None
    city: str | None = None
    source: str | None = None


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


def _graphemes(s: str) -> list[str]:
    """Approximate extended grapheme clusters: base + combining marks/variation selectors/skin tones, ZWJ joins."""
    out: list[str] = []
    join_next = False
    for ch in s:
        cp = ord(ch)
        extend = (
            unicodedata.combining(ch) > 0
            or 0xFE00 <= cp <= 0xFE0F
            or 0x1F3FB <= cp <= 0x1F3FF
            or 0xE0020 <= cp <= 0xE007F
            or ch == "‍"
        )
        if out and (extend or join_next):
            out[-1] += ch
        else:
            out.append(ch)
        join_next = ch == "‍"
    return out


def truncate_bytes(s: str, limit: int) -> str:
    out, used = [], 0
    for g in _graphemes(s):
        n = len(g.encode("utf-8"))
        if used + n > limit:
            break
        out.append(g)
        used += n
    return "".join(out)


def _fix_reserved(s: str) -> str:
    base, dot, rest = s.partition(".")
    if base.upper() in _RESERVED:
        return f"{base}_{dot}{rest}"
    return s


def sanitize_segment(value: str, limit: int = MAX_SEGMENT_BYTES) -> str:
    """Turn any string into one safe path segment (never empty, '.', '..', reserved, or > limit bytes)."""
    s = unicodedata.normalize("NFC", value)
    s = "".join(ch for ch in s if ch not in _REMOVE)
    s = "".join("_" if ch in _REPLACE else ch for ch in s)
    s = s.strip().rstrip(". ").strip()
    if not s or set(s) <= {"."}:
        return "_"
    s = _fix_reserved(s)
    return truncate_bytes(s, limit) or "_"


class TemplateError(ValueError):
    def __init__(self, code: str, pos: int = 0) -> None:
        super().__init__(f"{code} at {pos}")
        self.code = code
        self.pos = pos


@dataclass(frozen=True)
class PathTooLong:
    length: int
    limit: int
    conflict: str = "path_too_long"
    fix: str = "fs.long_paths"


def check_full_path(target_abs: str, rel: str, platform: str, long_paths: bool) -> PathTooLong | None:
    return None


def split_name(name: str) -> tuple[str, str]:
    return name, ""


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
        elif p.var == "ext":
            out.append(values.ext)
        else:
            raw = getattr(values, p.var or "", None)
            if raw in (None, ""):
                raw = (fallback_values or {}).get(p.var or "") or _UNKNOWN.get(lang, _UNKNOWN["en"])
            out.append(sanitize_segment(str(raw)))
    return RenderResult(rel="".join(out), date_source=source if tpl.uses_date else None)
