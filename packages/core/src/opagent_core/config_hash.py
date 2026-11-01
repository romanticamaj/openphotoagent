"""config_hash / model_fingerprint / cache key (docs/02 §3.2, ADR-003, invariant 7).

Canonical JSON: keys sorted, no whitespace, UTF-8, strings NFC, integral floats as ints, shortest
round-trip float repr; NaN/Infinity rejected. Hash = BLAKE3 hex. Thresholds and output rules are
*not* part of ``config_hash`` (changing them never triggers re-inference, CORE-HASH-007/008).
"""

from __future__ import annotations

import json
import math
import unicodedata
from typing import Any

import blake3

# fields of task_config that influence model output (02 §3.2 task_config)
INFERENCE_FIELDS = (
    "task_type", "model_version", "prompt_template_ref", "generation_params", "label_set",
    "structured_output_schema", "run_if", "preprocess",
)


class ConfigError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _norm(v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ConfigError("CONFIG_NON_FINITE")
        return int(v) if v.is_integer() and abs(v) < 2**53 else v
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        return unicodedata.normalize("NFC", v)
    if isinstance(v, dict):
        return {unicodedata.normalize("NFC", str(k)): _norm(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_norm(x) for x in v]
    raise ConfigError("CONFIG_UNSUPPORTED_TYPE")


def canonical_json(value: Any) -> str:
    return json.dumps(_norm(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _b3(s: str) -> str:
    return blake3.blake3(s.encode("utf-8")).hexdigest()


def config_hash(task_config: dict[str, Any]) -> str:
    relevant = {k: task_config[k] for k in INFERENCE_FIELDS if k in task_config}
    return _b3(canonical_json(relevant))


def model_fingerprint(adapter_key: str, adapter_version: str, weights_sha256: str, numerics_epoch: int,
                      params: dict[str, Any] | None = None, pack_version: str | None = None) -> str:
    """``pack_version`` is deliberately ignored: a runtime pack rebuild with the same ``numerics_epoch`` keeps
    cached results valid (CORE-HASH-009, 08 §4.3)."""
    return _b3(canonical_json({"adapter": adapter_key, "adapter_version": adapter_version, "weights": weights_sha256,
                               "numerics_epoch": numerics_epoch, "params": params or {}}))


def cache_key(asset_hash: str, cfg_hash: str, fingerprint: str) -> str:
    return _b3(canonical_json([asset_hash, cfg_hash, fingerprint]))
