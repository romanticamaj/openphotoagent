"""Lunar New Year (正月初一) dates for the search planner (ADR-0008). Table: ``lunar_table.py`` (generated)."""

from __future__ import annotations

from datetime import date

from .lunar_table import DRAGON_BOAT, FIRST_YEAR, LANTERN, MID_AUTUMN, NEW_YEAR, QIXI


def lunar_new_year(year: int) -> date | None:
    """The Gregorian date of 正月初一 in ``year``, or None outside 1900–2099."""
    i = year - FIRST_YEAR
    if 0 <= i < len(NEW_YEAR):
        m, d = NEW_YEAR[i]
        return date(year, m, d)
    return None


def mid_autumn(year: int) -> date | None:
    """The Gregorian date of 中秋 (八月十五) in ``year``, or None outside 1900–2099 (SRCH-PLAN-028)."""
    i = year - FIRST_YEAR
    if 0 <= i < len(MID_AUTUMN):
        m, d = MID_AUTUMN[i]
        return date(year, m, d)
    return None


def _on(table: tuple[tuple[int, int], ...], year: int) -> date | None:
    i = year - FIRST_YEAR
    if 0 <= i < len(table):
        m, d = table[i]
        return date(year, m, d)
    return None


def dragon_boat(year: int) -> date | None:
    """端午 (五月初五) in ``year`` (SRCH-PLAN-030)."""
    return _on(DRAGON_BOAT, year)


def lantern(year: int) -> date | None:
    """元宵 (正月十五) in ``year`` (SRCH-PLAN-030)."""
    return _on(LANTERN, year)


def qixi(year: int) -> date | None:
    """七夕 (七月初七) in ``year`` (SRCH-PLAN-030)."""
    return _on(QIXI, year)
