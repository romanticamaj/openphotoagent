"""Sidecar JSON schema `<stem>.opagent.json` (docs/02 §6.2, OUT-EXE-020)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Provenance(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    fingerprint: str | None = None


class SidecarV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    category: str | None
    tags: list[str]
    caption: str | None
    date_source: Literal["exif_offset", "exif_gps_tz", "exif_local_assumed", "mtime"] | None
    provenance: Provenance
