"""Preset files you can share (04 §2.2; VERSIONING §4), pure.

* Export: a ``kind: opagent-preset`` document with ``schema_version`` 2 around the preset. Version 1 is the bare
  preset file (the built-in format of v0.1–v0.2); importing it migrates it.
* Fields this version does not know are kept as they are, so a file from a newer minor survives a round trip.
* Import is untrusted input: YAML is parsed safely with no tags and no aliases (no object construction, no
  "billion laughs"), the size is capped, and every path template and rule expression is validated with the same
  code that runs them (CORE-TPL-018, CORE-RULE-006) before anything is stored.
"""

from __future__ import annotations

import copy
import re
from typing import Any

import yaml

from .path_template import TemplateError, compile_template
from .rules import RuleError, compile_when

SCHEMA_VERSION = 2
KIND = "opagent-preset"
MAX_BYTES = 256 * 1024
ACTIONS = frozenset({"copy", "move", "symlink", "hardlink", "sidecar_json", "xmp", "trash"})
CONFLICTS = frozenset({"rename", "skip", "overwrite_if_same", "ask"})
KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class PresetError(ValueError):
    def __init__(self, code: str, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


class _NoAliasLoader(yaml.SafeLoader):  # type: ignore[misc]  # PyYAML ships no stubs here
    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise PresetError("PRESET_INVALID", reason="yaml_alias")
        event = self.peek_event()
        anchor = getattr(event, "anchor", None)
        if anchor is not None:
            raise PresetError("PRESET_INVALID", reason="yaml_anchor")
        tag = getattr(event, "tag", None)
        if (tag and not tag.startswith("tag:yaml.org,2002:")) or (tag and "python" in tag):
            raise PresetError("PRESET_INVALID", reason="yaml_tag")
        return super().compose_node(parent, index)


def _parse(text: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise PresetError("PRESET_TOO_LARGE", max=MAX_BYTES)
    try:
        d = yaml.load(text, Loader=_NoAliasLoader)  # noqa: S506 - a SafeLoader subclass that also refuses aliases
    except PresetError:
        raise
    except yaml.YAMLError as e:
        raise PresetError("PRESET_INVALID", reason="yaml", detail=str(e)[:200]) from None
    if not isinstance(d, dict):
        raise PresetError("PRESET_INVALID", reason="not_a_mapping")
    return d


def _validate(p: dict[str, Any]) -> None:
    if not isinstance(p.get("key"), str) or not KEY.match(p["key"]):
        raise PresetError("PRESET_INVALID", reason="key", pointer="/preset/key")
    rules = p.get("output_rules") or []
    if not isinstance(rules, list):
        raise PresetError("PRESET_INVALID", reason="output_rules", pointer="/preset/output_rules")
    for i, r in enumerate(rules):
        at = f"/preset/output_rules/{i}"
        if not isinstance(r, dict):
            raise PresetError("PRESET_INVALID", reason="rule", pointer=at)
        if r.get("action", "copy") not in ACTIONS:
            raise PresetError("PRESET_INVALID", reason="action", pointer=at + "/action")
        if r.get("conflict_policy", "rename") not in CONFLICTS:
            raise PresetError("PRESET_INVALID", reason="conflict_policy", pointer=at + "/conflict_policy")
        try:
            compile_template(str(r.get("path_template", "{stem}{ext}")))
        except TemplateError as e:
            raise PresetError(e.code, pointer=at + "/path_template") from None
        try:
            compile_when(str(r.get("when", "true")), at + "/when")
        except RuleError as e:
            raise PresetError(e.code, pointer=at + "/when") from None
    for i, c in enumerate(p.get("categories") or []):
        if (
            not isinstance(c, dict)
            or not isinstance(c.get("key"), str)
            or not re.fullmatch(r"[a-z0-9_.-]{1,64}", c["key"])
        ):
            raise PresetError("PRESET_INVALID", reason="category", pointer=f"/preset/categories/{i}")


def _migrate(d: dict[str, Any]) -> tuple[dict[str, Any], int | None]:
    found = d.get("schema_version", 1)
    if not isinstance(found, int):
        raise PresetError("PRESET_INVALID", reason="schema_version")
    if found > SCHEMA_VERSION:
        raise PresetError("PRESET_TOO_NEW", found=found, supported=SCHEMA_VERSION)
    if found == 1 or d.get("kind") != KIND:  # v1: the bare preset file
        bare = {k: v for k, v in d.items() if k != "schema_version"}
        return {"schema_version": SCHEMA_VERSION, "kind": KIND, "preset": bare}, 1
    return d, None


def import_text(text: str) -> dict[str, Any]:
    doc, was = _migrate(_parse(text))
    pre = doc.get("preset")
    if not isinstance(pre, dict):
        raise PresetError("PRESET_INVALID", reason="preset", pointer="/preset")
    _validate(pre)
    out = {k: copy.deepcopy(v) for k, v in doc.items() if k not in ("app_version", "migrated_from")}
    if was is not None:
        out["migrated_from"] = was
    return out


def export_text(doc: dict[str, Any], app_version: str) -> str:
    body = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "app_version": app_version,
        **{k: v for k, v in doc.items() if k not in ("schema_version", "kind", "app_version", "migrated_from")},
    }
    return str(yaml.safe_dump(body, allow_unicode=True, sort_keys=False))
