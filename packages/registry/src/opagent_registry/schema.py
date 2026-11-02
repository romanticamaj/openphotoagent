"""Registry schema (03 §1.1–1.2, 06 §1.1, ADR-0002). Pydantic models double as the JSON Schema."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SLUG = r"^[a-z0-9][a-z0-9._-]*$"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
Kind = Literal[
    "vlm", "embedding", "tagger", "captioner", "face_detector", "face_embedder", "face_quality", "ocr", "reranker"
]
Runtime = Literal["vllm", "llama.cpp", "ollama", "transformers", "onnx", "openai-compatible", "mlx"]
WEIGHT_EXT = (".gguf", ".onnx", ".safetensors")  # invariant 10: no pickle weights


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class License(_M):
    spdx: str
    weights: str | None = None
    code: str | None = None
    commercial_use: bool = False
    redistributable: bool = False
    requires_acceptance: bool = False
    biometric: bool = False
    url: str | None = None


class Source(_M):
    hf_repo: str | None = None
    revision: str = "main"


class FileRef(_M):
    repo: str
    path: str
    role: str | None = None
    size: int | None = Field(default=None, gt=0)
    sha256: str | None = None

    @field_validator("path")
    @classmethod
    def _no_pickle(cls, v: str) -> str:
        low = v.lower()
        if low.endswith((".bin", ".pt", ".pth", ".pkl", ".ckpt")):
            raise ValueError(f"pickle weights are not accepted: {v} (only safetensors/gguf/onnx)")
        return v

    @field_validator("sha256")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        if v is not None and not HEX64.match(v):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return v


class VramSpec(_M):
    weights: float = 0.0
    mmproj: float = 0.0
    runtime_overhead: float = 0.0
    kv_per_1k_tokens: float = 0.0

    @property
    def need_gb(self) -> float:
        return self.weights + self.mmproj + self.runtime_overhead


class Requires(_M):
    runtime_pack: list[str] = []
    os: list[Literal["windows", "macos", "linux"]] = ["windows", "macos", "linux"]
    min_ram_gb: float = 0.0
    min_compute_capability: str | None = None
    accel_any: list[str] = []


class Variant(_M):
    id: str = Field(pattern=SLUG)
    runtime: Runtime
    quant: str | None = None
    device: str | None = None
    revision: str = "main"
    files: list[FileRef] = []
    vram_gb: VramSpec = VramSpec()
    ram_gb: float | None = None
    recommended_batch: int = 1
    requires: Requires = Requires()
    perf_prior: dict[str, Any] | None = None
    endpoint: dict[str, Any] | None = None
    launch: dict[str, Any] | None = None
    cpu_moe_layers: int | None = None
    trust_remote_code: bool = False


class ModelSpec(_M):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=SLUG)
    display_name: str
    family: str
    kind: Kind
    capabilities: list[str]
    languages: list[str] = []
    license: License
    source: Source = Source()
    structured_output: Literal["none", "json_object", "json_schema", "grammar"] = "none"
    supports_logprobs: bool = False
    max_images_per_request: int = 1
    context_length: int | None = None
    image: dict[str, int] | None = None
    embedding: dict[str, Any] | None = None
    tagger: dict[str, Any] | None = None
    tasks_default_rank: dict[str, int] = {}
    tiers: list[str] = []
    defaults: dict[str, Any] = {}
    privacy: dict[str, Any] = {}
    variants: list[Variant] = Field(min_length=1)
    notes: str = ""

    def variant(self, vid: str) -> Variant:
        for v in self.variants:
            if v.id == vid:
                return v
        raise KeyError(f"{self.id}@{vid}")


class PackPlatform(_M):
    os: Literal["windows", "macos", "linux"]
    arch: Literal["x86_64", "arm64"]
    url: str | None = None
    size: int | None = None
    sha256: str | None = None
    kind: str | None = None


class PackRequires(_M):
    nvidia_driver_min: str | None = None
    compute_capability_min: str | None = None
    cuda_architectures: list[str] | None = None


class RuntimePack(_M):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=SLUG)
    provides: dict[str, str]
    builtin: bool = False
    advanced: bool = False
    version: str | None = None
    size_est: int | None = None
    platforms: list[PackPlatform]
    requires: PackRequires = PackRequires()
    self_test: dict[str, Any] | None = None
    license: dict[str, str] = {}
    install_dir: str | None = None
    notes: str = ""

    @property
    def accel(self) -> str:
        return self.provides.get("accel", "cpu")

    def supports(self, os: str, arch: str) -> bool:
        return any(p.os == os and p.arch == arch for p in self.platforms)


class GpuClass(_M):
    id: str
    match: list[str]
    bandwidth_gbs: float
    fp16_tflops: float | None = None
    compute_capability: str | None = None


class GpuClasses(_M):
    schema_version: Literal[1] = 1
    classes: list[GpuClass]


class PinnedFile(_M):
    path: str
    size: int = Field(gt=0)
    sha256: str
    lfs_oid: str | None = None

    @field_validator("sha256")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not HEX64.match(v):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return v


class Pin(_M):
    model: str
    variant: str
    revision: str
    files: list[PinnedFile] = Field(min_length=1)

    @field_validator("revision")
    @classmethod
    def _commit(cls, v: str) -> str:
        if not HEX40.match(v):
            raise ValueError("revision must be a 40-hex commit sha (never a branch)")
        return v


class PinsLock(_M):
    schema_version: Literal[1] = 1
    pins: list[Pin] = []
