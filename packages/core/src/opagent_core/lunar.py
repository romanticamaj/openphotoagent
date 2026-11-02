"""Lunar New Year (正月初一) dates for the search planner (ADR-0008). Table: ``lunar_table.py`` (generated)."""

from __future__ import annotations

from datetime import date

from .lunar_table import FIRST_YEAR, NEW_YEAR


def lunar_new_year(year: int) -> date | None:
    """The Gregorian date of 正月初一 in ``year``, or None outside 1900–2099."""
    i = year - FIRST_YEAR
    if 0 <= i < len(NEW_YEAR):
        m, d = NEW_YEAR[i]
        return date(year, m, d)
    return None
