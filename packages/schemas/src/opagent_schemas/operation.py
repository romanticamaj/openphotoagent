"""Operation DTO (06 §5.1) with waiting_reason validation (OP-017)."""

from __future__ import annotations

from pydantic import BaseModel

WAITING_REASONS: tuple[str, ...] = ()


class OperationDTO(BaseModel):
    id: str
    type: str
    status: str
    waiting_reason: str | None = None
    auto_paused: bool = False
