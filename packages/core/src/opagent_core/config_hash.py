"""config_hash / model_fingerprint / cache key (docs/02 §3.2, ADR-003, invariant 7)."""

from __future__ import annotations

from typing import Any


class ConfigError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_json(value: Any) -> str:
    return ""


def config_hash(task_config: dict[str, Any]) -> str:
    return ""


def model_fingerprint(adapter_key: str, adapter_version: str, weights_sha256: str, numerics_epoch: int,
                      params: dict[str, Any] | None = None, pack_version: str | None = None) -> str:
    return ""


def cache_key(asset_hash: str, cfg_hash: str, fingerprint: str) -> str:
    return ""
