"""Pure organize planner (docs/02 §4.1 stage 6): assets + output rule -> planned copy actions."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from datetime import datetime

from .path_template import DateSource, RenderValues, compile_template, render, split_name

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
    conflict: str = "none"


def plan(items: list[PlanItem], preset: str, target_sub: str = "") -> list[PlannedAction]:
    tpl = compile_template(PRESET_TEMPLATES[preset])
    out: list[PlannedAction] = []
    for it in items:
        stem, ext = split_name(posixpath.basename(it.src_rel))
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




def suffixed(rel: str, n: int) -> str:
    """``dir/IMG_0001.jpg`` -> ``dir/IMG_0001 (n).jpg`` (OUT-EXE-006)."""
    d, name = posixpath.split(rel)
    stem, ext = split_name(name)
    return posixpath.join(d, f"{stem} ({n}){ext}") if d else f"{stem} ({n}){ext}"


def assign_collisions(actions: list[PlannedAction], case_insensitive: bool = False) -> list[PlannedAction]:
    """Plan-time collision handling (OUT-EXE-009): two different photos never target the same name; the same
    content targeting the same name stays a single file (the second copy is skipped as ``exists_same``)."""
    from dataclasses import replace

    def key(rel: str) -> str:
        return rel.casefold() if case_insensitive else rel

    owner: dict[str, str] = {}  # key -> content hash
    out: list[PlannedAction] = []
    for a in actions:
        k = key(a.dst_rel)
        if k not in owner or owner[k] == a.src_hash:
            owner.setdefault(k, a.src_hash)
            out.append(a)
            continue
        n = 1
        while key(suffixed(a.dst_rel, n)) in owner:
            n += 1
        new = suffixed(a.dst_rel, n)
        owner[key(new)] = a.src_hash
        out.append(replace(a, dst_rel=new, conflict="name_collision"))
    return out
