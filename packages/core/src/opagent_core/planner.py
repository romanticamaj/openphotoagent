"""Pure organize planner (docs/02 §4.1 stage 6): assets + output rule -> planned copy actions."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from datetime import datetime

from .path_template import DateSource, RenderValues, compile_template, render

PRESET_TEMPLATES = {"by-date": "{taken_at:%Y}/{taken_at:%Y-%m}/{stem}{ext}"}


@dataclass(frozen=True)
class PlanItem:
    asset_id: str
    src_rel: str
    content_hash: str
    taken_at: datetime | None
    taken_at_source: DateSource | None
    mtime: datetime


@dataclass(frozen=True)
class PlannedAction:
    asset_id: str
    src_rel: str
    dst_rel: str
    src_hash: str
    date_source: DateSource | None


def split_name(rel: str) -> tuple[str, str]:
    stem, ext = posixpath.splitext(posixpath.basename(rel))
    return stem, ext


def plan(items: list[PlanItem], preset: str, target_sub: str = "") -> list[PlannedAction]:
    tpl = compile_template(PRESET_TEMPLATES[preset])
    out: list[PlannedAction] = []
    for it in items:
        stem, ext = split_name(it.src_rel)
        r = render(
            tpl,
            RenderValues(
                stem=stem,
                ext=ext,
                taken_at=it.taken_at,
                taken_at_source=it.taken_at_source or "exif_offset",
                mtime=it.mtime,
            ),
        )
        dst = posixpath.join(target_sub, r.rel) if target_sub else r.rel
        out.append(PlannedAction(it.asset_id, it.src_rel, dst, it.content_hash, r.date_source))
    return out


def summarize(actions: list[PlannedAction]) -> dict[str, int]:
    folders = {posixpath.dirname(a.dst_rel) for a in actions}
    return {"copy": len(actions), "folders": len(folders), "total": len(actions)}
