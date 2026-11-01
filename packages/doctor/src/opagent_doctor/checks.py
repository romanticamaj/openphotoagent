from dataclasses import dataclass, field
from typing import Any

CHECKS: dict[str, Any] = {}


@dataclass
class Ctx:
    probes: dict[str, Any]
    env: dict[str, Any] = field(default_factory=dict)
    clock: Any = None


async def run_checks(ctx: Ctx, **kw: Any) -> list[Any]:
    return []


def features(results: list[Any]) -> dict[str, Any]:
    return {}
