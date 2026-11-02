"""Rule-based query planner (05 §2.3). Stub — see tests."""

from __future__ import annotations

from typing import Any


def plan(q: str, **_: Any) -> dict[str, Any]:
    return {"text": q, "filters": {}, "planner": {"source": "rules", "confidence": 0.0, "explain": []}}
