"""Stub."""

from __future__ import annotations

from typing import Any


class AutoApproveError(ValueError):
    code = ""
    actions: list[str] = []


def validate_auto_approve(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg
