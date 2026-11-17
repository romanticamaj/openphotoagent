"""Memories (10 §2.8): pure decisions over photo metadata, no I/O. The API reads the rows and asks these at request
time (ADR-0049); nothing here reads a clock, a file or a model."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Theme:
    year: int
    key: str  # a vocabulary id: 「2025 的足球」 is (2025, activity.soccer)
    count: int
    asset_ids: tuple[str, ...]  # at most ``shown``, spread over the theme in the given order


def _spread[T](ids: list[T], n: int) -> list[T]:
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
    place_or_thing: Callable[[str], bool] | None = None,
) -> list[Theme]:
    """主題 (10 §2.8, MEM-009): per year, newest first, the vocabulary tags on at least ``min_count`` photos, most
    first, at most ``per_year``. A tag on more than ``common_share`` of the year's photos (a child, outdoors) says
    nothing about that year and is left out. ``rows`` are (asset_id, year, tags) in the order photos should appear
    (by date); only vocabulary ids (``group.name``) count, never a free word, and not those in ``skip``: terms the
    vocabulary marks as true of any year (室內, 玩耍; MEM-012). A theme whose photos are more than ``max_overlap``
    another, higher theme's is skipped; the count is every photo with the tag, as 「看全部」 shows it, and only the
    photos shown leave out ``hidden`` (near-duplicates; MEM-013). With ``place_or_thing``, a year whose first
    ``per_year - 1`` themes have no place or thing gives its last slot to the strongest one that qualifies; with none,
    the slot is filled as before (MEM-028, ADR-0049 addendum 7: the 6 themes were all about people)."""
    by_year: dict[int, list[tuple[str, list[str]]]] = {}
    for aid, year, tags in rows:
        by_year.setdefault(year, []).append((aid, tags))
    out: list[Theme] = []
    for year in sorted(by_year, reverse=True):
        photos = by_year[year]
        counts = Counter(k for _, tags in photos for k in set(tags) if "." in k and k not in skip)
        taken: list[set[str]] = []
        picked: list[Theme] = []
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

        def take(
            k: str,
            n: int,
            photos: list[tuple[str, list[str]]] = photos,
            taken: list[set[str]] = taken,
            picked: list[Theme] = picked,
            year: int = year,
        ) -> bool:
            if n < min_count or n > common_share * len(photos):
                return False
            ids = [aid for aid, tags in photos if k in tags]
            mine = set(ids)
            if any(len(mine & other) > max_overlap * len(mine) for other in taken):
                return False
            taken.append(mine)
            shown_ids = [a for a in ids if a not in hidden]
            picked.append(Theme(year=year, key=k, count=n, asset_ids=tuple(_spread(shown_ids, shown))))
            return True

        reserve = place_or_thing is not None and per_year >= 2
        for k, n in ranked:
            if len(picked) == (per_year - 1 if reserve else per_year):
                break
            take(k, n)
        if reserve and len(picked) == per_year - 1:
            done = {t.key for t in picked}
            if not any(place_or_thing(k) for k in done):  # type: ignore[misc]
                for k, n in ranked:
                    if k not in done and place_or_thing(k) and take(k, n):  # type: ignore[misc]
                        break
            if len(picked) < per_year:
                for k, n in ranked:
                    if k not in {t.key for t in picked} and take(k, n):
                        break
        out.extend(sorted(picked, key=lambda t: (-t.count, t.key)))
    return out


@dataclass(frozen=True)
class Event:
    day: date
    key: str | None  # the subject most of its photos share, or none: named by the date only
    count: int
    asset_ids: tuple[str, ...]
    last: date | None = None  # the last day, when the outing ran over days next to each other (MEM-018)


def events(
    rows: list[tuple[str, date, list[str]]],
    *,
    min_photos: int = 15,
    shown: int = 12,
    skip: set[str] | frozenset[str] = frozenset(),
    share: float = 0.4,
    named_only: bool = False,
    prefer: Callable[[str, str | None], bool] | None = None,
) -> list[Event]:
    """活動 (MEM-015, ADR-0049 addendum 2): a day with at least ``min_photos`` photos is an event, newest first. It
    is named by the vocabulary tag on the most of its photos, at least ``share`` of them, leaving out ``skip`` (terms
    true of any day); with none, by its date alone. ``rows`` are (asset_id, local day, tags) in date order."""
    by_day: dict[date, list[tuple[str, list[str]]]] = {}
    for aid, day, tags in rows:
        by_day.setdefault(day, []).append((aid, tags))
    found: list[tuple[date, date, str | None, list[str]]] = []  # first day, last day, subject, every photo
    tally: list[Counter[str]] = []  # each found event's tag counts over all its days: its name (MEM-021)
    edge: list[tuple[Counter[str], int, str | None]] = []  # the day an event would join by: its counts, size, subject
    for day in sorted(by_day, reverse=True):
        photos = by_day[day]
        if len(photos) < min_photos:
            continue
        counts = Counter(k for _, tags in photos for k in set(tags) if "." in k and k not in skip)
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        key = top[0][0] if top and top[0][1] >= share * len(photos) else None
        if key is None and named_only:
            continue  # many photos with nothing in common: an import day, not an outing (MEM-018)
        ids = [aid for aid, _ in photos]
        if found and key and (found[-1][0] - day).days == 1:
            # the day next to it is the same outing when either day's subject is on enough of the other's photos
            # (MEM-018: two 「手工」 cards; MEM-021: 4/8 手工 and 4/9 玩具 of one craft-and-toy outing)
            # weighed against the day next to it, not the whole run (MEM-022)
            _, last, _, more = found[-1]
            near, size, other = edge[-1]
            # both ways: each day's subject on enough of the other's photos (MEM-023: a toy day joined a football run by
            # its toys on the next day, while football was on 12% of it; audit rounds 101-102)
            if other and (counts.get(other, 0) >= share * len(photos) and near.get(key, 0) >= share * size):
                both = tally[-1] + counts
                name = max((k for k in both if k in (key, other, found[-1][2])), key=lambda k: (both[k], k))
                found[-1] = (day, last, name, ids + more)
                tally[-1] = both
                edge[-1] = (counts, len(photos), key)
                continue
        found.append((day, day, key, ids))
        tally.append(counts)
        edge.append((counts, len(photos), key))

    def picked(ids: list[str], key: str | None) -> tuple[str, ...]:
        # the cover is the first photo ``prefer`` says shows the subject, from the whole outing (MEM-019)
        lead = next((a for a in ids if prefer(a, key)), None) if prefer else None
        if lead is None:
            return tuple(_spread(ids, shown))
        return (lead, *_spread([a for a in ids if a != lead], shown - 1))

    return [
        Event(day=first, key=key, count=len(ids), asset_ids=picked(ids, key), last=last if last != first else None)
        for first, last, key, ids in found
    ]
