"""InferenceBackend protocol and DTOs (docs/02 §4.4, docs/03 §2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

TaskType = Literal["caption", "tag", "classify", "embed"]
ErrorCode = Literal["OOM", "TIMEOUT", "BAD_INPUT", "RUNTIME"]

# 06 §4.7 error codes for each InferenceError code (PF-081)
ERROR_CODE_MAP: dict[str, str] = {
    "OOM": "MODEL_OOM",
    "TIMEOUT": "RUNTIME_TIMEOUT",
    "BAD_INPUT": "DECODE_FAILED",
    "RUNTIME": "RUNTIME_CRASHED",
}


class InferenceError(Exception):
    def __init__(self, code: ErrorCode, retryable: bool, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.retryable = retryable

    @property
    def error_code(self) -> str:
        return ERROR_CODE_MAP[self.code]


class ModelUnavailable(Exception):  # noqa: N818 - name from 03 §2.3
    """Model not loaded / not installed."""


@dataclass(frozen=True)
class PreferredInput:
    max_side: int
    format: Literal["rgb", "jpeg", "png"]


@dataclass(frozen=True)
class BackendInfo:
    adapter_key: str
    model_fingerprint: str
    task_types: tuple[TaskType, ...]
    preferred_input: PreferredInput
    max_batch: int
    est_vram_mb: int
    supports_structured_output: bool
    embedding_dim: int | None = None


@dataclass
class LoadOptions:
    device: str = "auto"
    max_batch: int | None = None


@dataclass
class HealthStatus:
    ok: bool
    vram_used_mb: int


@dataclass(frozen=True)
class ImageInput:
    item_id: str
    content_hash: str
    data: bytes  # already preprocessed in memory; adapters never see paths (INF-CONF-011)
    labels: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchRequest:
    task_type: TaskType
    items: list[ImageInput]
    prompt_template_ref: str = "builtin:fused_v1"
    variables: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    generation_params: dict[str, Any] = field(default_factory=dict)
    deadline_s: float = 60.0


@dataclass
class ItemOutput:
    item_id: str
    caption: str | None = None
    tags: list[tuple[str, float]] | None = None
    category: str | None = None
    confidence: float | None = None
    embedding: list[float] | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class InferenceBackend(Protocol):
    def describe(self) -> BackendInfo: ...

    async def load(self, opts: LoadOptions) -> None: ...

    async def unload(self) -> None: ...

    async def health(self) -> HealthStatus: ...

    async def run_batch(self, req: BatchRequest) -> list[ItemOutput]: ...
