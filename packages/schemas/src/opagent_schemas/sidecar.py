"""Sidecar JSON schema (docs/02 §6.2, OUT-EXE-020)."""

from __future__ import annotations

from pydantic import BaseModel


class SidecarV1(BaseModel):
    schema_version: int
