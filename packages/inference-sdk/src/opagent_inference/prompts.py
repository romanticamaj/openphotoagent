from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaptionConfig:
    length: str = "short"
    languages: list[str] = field(default_factory=lambda: ["zh-Hant"])


def render_fused_prompt(cats: Any, cfg: CaptionConfig) -> Any:
    return None
