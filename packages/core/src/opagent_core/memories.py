"""Memories (10 §2.8): pure decisions over photo metadata, no I/O. The API reads the rows and asks these at request
time (ADR-0049); nothing here reads a clock, a file or a model."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    year: int
    key: str  # a vocabulary id: 「2025 的足球」 is (2025, activity.soccer)
    count: int
    asset_ids: tuple[str, ...]  # at most ``shown``, spread over the theme in the given order


def _spread(ids: list[str], n: int) -> list[str]:
    """``n`` of ``ids`` evenly over the list, first and last included."""
    if len(ids) <= n:
        return list(ids)
    step = (len(ids) - 1) / (n - 1)
    return [ids[round(i * step)] for i in range(n)]


def themes(
    rows: list[tuple[str, int, list[str]]],
    *,
    min_count: int = 8,
    per_year: int = 3,
    common_share: float = 0.6,
    shown: int = 12,
    skip: set[str] | frozenset[str] = frozenset(),
    max_overlap: float = 1.0,
    hidden: set[str] | frozenset[str] = frozenset(),
) -> list[Theme]:
    """主題 (10 §2.8, MEM-009): per year, newest first, the vocabulary tags on at least ``min_count`` photos, most
    first, at most ``per_year``. A tag on more than ``common_share`` of the year's photos (a child, outdoors) says
    nothing about that year and is left out. ``rows`` are (asset_id, year, tags) in the order photos should appear
    (by date); only vocabulary ids (``group.name``) count, never a free word, and not those in ``skip``: terms the
    vocabulary marks as true of any year (室內, 玩耍; MEM-012). A theme whose photos are more than ``max_overlap``
    another, higher theme's is skipped; the count is every photo with the tag, as 「看全部」 shows it, and only the
    photos shown leave out ``hidden`` (near-duplicates; MEM-013)."""
    by_year: dict[int, list[tuple[str, list[str]]]] = {}
    for aid, year, tags in rows:
        by_year.setdefault(year, []).append((aid, tags))
    out: list[Theme] = []
    for year in sorted(by_year, reverse=True):
        photos = by_year[year]
        counts = Counter(k for _, tags in photos for k in set(tags) if "." in k and k not in skip)
        taken: list[set[str]] = []
        for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            if len(taken) == per_year:
                break
            if n < min_count or n > common_share * len(photos):
                continue
            ids = [aid for aid, tags in photos if k in tags]
            mine = set(ids)
            if any(len(mine & other) > max_overlap * len(mine) for other in taken):
                continue
            taken.append(mine)
            shown_ids = [a for a in ids if a not in hidden]
            out.append(Theme(year=year, key=k, count=n, asset_ids=tuple(_spread(shown_ids, shown))))
    return out
