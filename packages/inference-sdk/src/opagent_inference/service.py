from dataclasses import dataclass
from typing import Any


@dataclass
class Variant:
    key: str
    factory: Any
    pack: str | None = None


class ResilientRunner:
    def __init__(self, chain: list[Variant], pixel_ladder: Any = None, on_attempt: Any = None) -> None:
        self.provenance: list[Any] = []
        self.batch_size = 0
        self.current = chain[0]

    async def run(self, req: Any, reprepare: Any = None) -> list[Any]:
        return []

    def choose(self, v: Variant) -> None: ...
