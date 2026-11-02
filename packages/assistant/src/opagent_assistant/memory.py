"""Conversation working set (10 §8.7): a follow-up like 「只要有狗的」 refines the previous filters."""

from __future__ import annotations

from typing import Any


def merge(working: dict[str, Any], delta: dict[str, Any], reset: bool = False) -> dict[str, Any]:
    if reset:
        return dict(delta)
    out = dict(working)
    out.update(delta)
    return out
