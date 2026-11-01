"""Output-rule DSL, rule evaluation, presets and final_label (docs/02 §3.2, 03 §10; 09a §1.5). Pure.

The ``when`` DSL is parsed with :mod:`ast` and interpreted over a whitelist — never ``eval``'d. Allowed:
whitelisted field names (``exif.<field>`` included), literals, lists, comparisons, ``in``, ``and``/``or``/``not``.
"""

from __future__ import annotations

import ast
import operator
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import yaml

FIELDS = {"category", "category_key", "decision", "confidence", "tags", "source", "taken_year",
          "exif__is_screenshot", "exif__camera_make", "exif__camera_model", "exif__has_gps"}
LITERALS = {"true": True, "false": False, "null": None, "True": True, "False": False, "None": None}
_DOTTED = re.compile(r"\bexif\.([A-Za-z_]+)\b")
_CMP: dict[type, Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
    ast.GtE: operator.ge, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
}


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
    conflict_policy: str = "rename"


@dataclass
class Preset:
    key: str
    task_configs: list[dict[str, Any]]
    output_rules: list[Rule]
    categories: list[dict[str, Any]] = field(default_factory=list)
    name: dict[str, str] = field(default_factory=dict)


def _check(node: ast.AST, pointer: str) -> None:
    col = getattr(node, "col_offset", 0) + 1
    if isinstance(node, ast.Expression):
        _check(node.body, pointer)
    elif isinstance(node, ast.BoolOp):
        for v in node.values:
            _check(v, pointer)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        _check(node.operand, pointer)
    elif isinstance(node, ast.Compare):
        if not all(type(o) in _CMP for o in node.ops):
            raise RuleError("RULE_FORBIDDEN", pointer, col)
        _check(node.left, pointer)
        for c in node.comparators:
            _check(c, pointer)
    elif isinstance(node, ast.Name):
        if node.id not in FIELDS and node.id not in LITERALS:
            raise RuleError("RULE_UNKNOWN_FIELD", pointer, col, node.id)
    elif isinstance(node, ast.Constant):
        if not isinstance(node.value, str | int | float | bool | type(None)):
            raise RuleError("RULE_FORBIDDEN", pointer, col)
    elif isinstance(node, ast.List | ast.Tuple):
        for e in node.elts:
            _check(e, pointer)
    else:
        raise RuleError("RULE_FORBIDDEN", pointer, col, type(node).__name__)


def _eval(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.BoolOp):
        vals = (bool(_eval(v, env)) for v in node.values)
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp):
        return not _eval(node.operand, env)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators, strict=True):
            right = _eval(comp, env)
            try:
                if not _CMP[type(op)](left, right):
                    return False
            except TypeError:
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        return LITERALS[node.id] if node.id in LITERALS else env.get(node.id)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List | ast.Tuple):
        return [_eval(e, env) for e in node.elts]
    raise RuleError("RULE_FORBIDDEN")  # unreachable after _check


def compile_when(expr: str, pointer: str = "/when") -> Callable[[dict[str, Any]], bool]:
    src = _DOTTED.sub(lambda m: f"exif__{m.group(1)}", expr)
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as e:
        raise RuleError("RULE_SYNTAX", pointer, e.offset or 1, str(e.msg)) from None
    _check(tree, pointer)

    def fn(item: dict[str, Any]) -> bool:
        env = {k: v for k, v in item.items() if k != "exif"}
        env.update({f"exif__{k}": v for k, v in (item.get("exif") or {}).items()})
        return bool(_eval(tree, env))

    return fn


def evaluate(rules: list[Rule], item: dict[str, Any], skip_unreviewed_to_other: bool = False) -> Rule | None:
    """First rule (by priority) whose ``when`` matches. ``require_reviewed`` rules hold back unreviewed photos —
    unless the user chose the escape hatch, which routes them to the fallback (「其他」) rule (CORE-RULE-004)."""
    unreviewed = item.get("decision") == "needs_review"
    for i, r in enumerate(sorted(rules, key=lambda r: r.priority)):
        if not compile_when(r.when, f"/output_rules/{i}/when")(item):
            continue
        if r.require_reviewed and unreviewed:
            if not skip_unreviewed_to_other:
                return None
            continue
        if skip_unreviewed_to_other and unreviewed and r.fallback_category is None:
            continue
        return r
    return None


def load_preset(text: str) -> Preset:
    d = yaml.safe_load(text)
    rules = []
    for i, r in enumerate(d.get("output_rules", [])):
        compile_when(str(r["when"]), f"/output_rules/{i}/when")
        rules.append(Rule(priority=int(r["priority"]), when=str(r["when"]), action=r.get("action", "copy"),
                          target_id=r.get("target_id"), path_template=r.get("path_template", "{stem}{ext}"),
                          require_reviewed=bool(r.get("require_reviewed", False)),
                          fallback_category=r.get("fallback_category"),
                          conflict_policy=r.get("conflict_policy", "rename")))
    return Preset(key=d["key"], task_configs=list(d.get("task_configs") or []), output_rules=rules,
                  categories=list(d.get("categories") or []), name=dict(d.get("name") or {}))


def validate_custom(categories: list[dict[str, Any]]) -> None:
    """Each custom category needs a description or at least one example photo (CORE-RULE-009)."""
    for i, c in enumerate(categories):
        if not (c.get("description") or "").strip() and not c.get("examples"):
            raise RuleError("CUSTOM_CATEGORY_UNDERSPECIFIED", f"/categories/{i}")


def final_label(result: dict[str, Any], review: dict[str, Any] | None) -> dict[str, Any]:
    """Human corrections win over model output (CORE-RULE-010)."""
    out = {k: result.get(k) for k in ("category", "tags", "caption")}
    if review and review.get("status") in ("corrected", "approved") and review.get("corrected"):
        out.update(review["corrected"])
        out["source"] = "review"
    else:
        out["source"] = "model"
    return out
