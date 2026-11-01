from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClassDef:
    id: str
    name: str
    description: str = ""
    examples: list[Any] = field(default_factory=list)
    negatives: list[Any] = field(default_factory=list)


@dataclass
class Taxonomy:
    classes: list[ClassDef]
    rules: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Item:
    image: Any
    exif: dict[str, Any]


class Cascade:
    def __init__(self, embedder: Any, vlm: Any = None, policy: Any = None, mode: str = "concurrent") -> None:
        self.events: list[Any] = []

    async def run(self, items: list[Item], tax: Taxonomy) -> list[Any]:
        return []


def estimate_distribution(embs: Any, tax: Taxonomy, texts: Any) -> Any:
    return None
