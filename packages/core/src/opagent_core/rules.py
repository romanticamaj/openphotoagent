"""Output-rule DSL, rule evaluation, presets and final_label (docs/02 §3.2, 03 §10; 09a §1.5). Pure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class RuleError(ValueError):
    def __init__(self, code: str, pointer: str = "", column: int = 0, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.pointer = pointer
        self.column = column


@dataclass(frozen=True)
class Rule:
    priority: int
    when: str
    action: str = "copy"
    target_id: str | None = None
    path_template: str = "{stem}{ext}"
    require_reviewed: bool = False
    fallback_category: str | None = None


@dataclass
class Preset:
    key: str
    task_configs: list[dict[str, Any]]
    output_rules: list[Rule]
    categories: list[dict[str, Any]] = field(default_factory=list)


def compile_when(expr: str, pointer: str = "/when") -> Any:
    return None


def evaluate(rules: list[Rule], item: dict[str, Any], skip_unreviewed_to_other: bool = False) -> Rule | None:
    return None


def load_preset(text: str) -> Preset:
    return Preset("", [], [])


def validate_custom(categories: list[dict[str, Any]]) -> None:
    return None


def final_label(result: dict[str, Any], review: dict[str, Any] | None) -> dict[str, Any]:
    return {}
