from dataclasses import dataclass, field
from typing import Any


class OutputParseError(ValueError):
    def __init__(self, raw: str = "") -> None:
        super().__init__("parse")
        self.raw = raw


@dataclass
class CategoryMap:
    keys: list[str]
    display: dict[str, dict[str, str]] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)


def fused_schema(cats: CategoryMap) -> dict[str, Any]:
    return {"properties": {}}


def parse_fused(raw: str, cats: CategoryMap, retry: Any = None, caption_lang: str | None = None) -> Any:
    raise OutputParseError(raw)
