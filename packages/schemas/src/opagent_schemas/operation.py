"""Operation DTO (06 §5.1) with waiting_reason validation (06 §5.5.7, OP-017)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, model_validator

WAITING_REASONS: tuple[str, ...] = (
    "QUEUED_BEHIND", "WAITING_GPU_SLOT", "WAITING_MODEL_DOWNLOAD", "WAITING_MODEL_LOAD", "WORKER_OFFLINE",
    "DISK_INSUFFICIENT_OUTPUT", "DISK_INSUFFICIENT_MODELS", "DISK_INSUFFICIENT_THUMBS", "NET_UNREACHABLE",
    "PAUSED_BY_USER", "PAUSED_ERROR_RATE", "PAUSED_ON_BATTERY", "PAUSED_GPU_CONTENDED", "PAUSED_NOT_IDLE",
    "SOURCE_OFFLINE", "FS_ROOT_UNAVAILABLE",
)
OpStatusLit = Literal["queued", "waiting", "running", "pausing", "paused", "cancelling", "succeeded", "failed",
                      "cancelled", "interrupted"]


class OperationDTO(BaseModel):
    id: str
    type: str
    status: OpStatusLit
    waiting_reason: str | None = None
    auto_paused: bool = False
    progress: dict[str, Any] = {}
    eta: dict[str, Any] | None = None
    seq: int = 0

    @model_validator(mode="after")
    def _reason(self) -> OperationDTO:
        if self.waiting_reason is not None and self.waiting_reason not in WAITING_REASONS:
            raise ValueError(f"unknown waiting_reason {self.waiting_reason}")
        needs = self.status == "waiting" or (self.status == "paused" and self.auto_paused)
        if needs and self.waiting_reason is None:
            raise ValueError("waiting_reason is required for waiting / auto-paused operations")
        return self
