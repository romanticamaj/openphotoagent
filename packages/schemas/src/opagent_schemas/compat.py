"""Compatibility of two versions of an interface schema (VERSIONING §4; 09b §9.1 FRZ-007).

The v1.0 contract snapshots (``contracts/``) are compared with what the code produces now. Every difference is
``additive`` (allowed within a major: needs an explicit snapshot update) or ``breaking`` (needs ``/api/v2``, a new
protocol major or a new manifest major, plus a migration note in the CHANGELOG).

``side`` says who reads the schema:

* ``request`` — the server reads it: new optional fields and wider enums are fine; new required fields, narrower
  enums, removed fields and type changes break callers.
* ``response`` — clients read it: new fields and new enum values are fine (clients ignore what they do not know);
  removing a field, making a required field optional, or changing a type breaks them.
* ``api`` — an OpenAPI document: paths, methods, parameters, request bodies (request side) and 2xx responses
  (response side), resolving ``$ref`` into ``components.schemas``.

Comparison is by key, never by position, so reordering is not a change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Change:
    kind: str  # "additive" | "breaking"
    path: str
    message: str


def breaking(changes: list[Change]) -> bool:
    return any(c.kind == "breaking" for c in changes)


def compare(old: Any, new: Any, side: str) -> list[Change]:
    if side == "api":
        return _api(old, new)
    out: list[Change] = []
    _schema(old, new, side, "", out, old, new)
    return out


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _resolve(s: Any, root: Any) -> Any:
    seen = 0
    while isinstance(s, dict) and "$ref" in s and seen < 32:
        ref = str(s["$ref"])
        node = root
        for part in ref.lstrip("#/").split("/"):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        s = node
        seen += 1
    return s


def _types(s: dict[str, Any]) -> set[str]:
    t = s.get("type")
    if isinstance(t, list):
        return {str(x) for x in t}
    if isinstance(t, str):
        return {t}
    if "anyOf" in s or "oneOf" in s:
        out: set[str] = set()
        for alt in s.get("anyOf") or s.get("oneOf") or []:
            if isinstance(alt, dict):
                out |= _types(alt) or {"*"}
        return out
    return set()


def _schema(old: Any, new: Any, side: str, path: str, out: list[Change], ro: Any, rn: Any) -> None:
    old, new = _resolve(old, ro), _resolve(new, rn)
    if not isinstance(old, dict) or not isinstance(new, dict):
        if old != new:
            out.append(Change("breaking", path, "schema replaced"))
        return
    to, tn = _types(old), _types(new)
    # widening a request type (e.g. string → string|null) is fine; anything else changes the wire format
    widened = (side == "request" and to <= tn) or (side == "response" and tn <= to)
    if to and tn and to != tn and not widened:
        out.append(Change("breaking", path, f"type {sorted(to)} → {sorted(tn)}"))
        return
    eo, en = old.get("enum"), new.get("enum")
    if isinstance(eo, list) and isinstance(en, list):
        removed = [v for v in eo if v not in en]
        added = [v for v in en if v not in eo]
        if removed and side == "request":
            out.append(Change("breaking", path, f"enum no longer accepts {removed}"))
        elif removed or added:
            out.append(Change("additive", path, f"enum +{added} -{removed}"))
    po, pn = old.get("properties") or {}, new.get("properties") or {}
    ro_req, rn_req = set(old.get("required") or []), set(new.get("required") or [])
    for k in sorted(set(po) | set(pn)):
        p = _join(_join(path, "properties"), k)
        if k not in pn:
            out.append(Change("breaking", p, "field removed"))
        elif k not in po:
            if side == "request" and k in rn_req:
                out.append(Change("breaking", p, "new required field"))
            else:
                out.append(Change("additive", p, "new field"))
        else:
            _schema(po[k], pn[k], side, p, out, ro, rn)
    if side == "request":
        for k in sorted((rn_req - ro_req) & set(po)):
            out.append(Change("breaking", _join(_join(path, "properties"), k), "field became required"))
    else:
        for k in sorted((ro_req - rn_req) & set(pn)):
            out.append(Change("breaking", _join(_join(path, "properties"), k), "field may now be missing"))
    if "items" in old or "items" in new:
        _schema(old.get("items", {}), new.get("items", {}), side, _join(path, "items"), out, ro, rn)
    ao, an = old.get("additionalProperties"), new.get("additionalProperties")
    if isinstance(ao, dict) and isinstance(an, dict):
        _schema(ao, an, side, _join(path, "additionalProperties"), out, ro, rn)


METHODS = ("get", "put", "post", "delete", "patch", "head", "options")


def _api(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    out: list[Change] = []
    po, pn = old.get("paths") or {}, new.get("paths") or {}
    for path in sorted(set(po) | set(pn)):
        if path not in pn:
            out.append(Change("breaking", path, "path removed"))
            continue
        if path not in po:
            out.append(Change("additive", path, "new path"))
            continue
        for m in METHODS:
            a, b = po[path].get(m), pn[path].get(m)
            where = f"{m.upper()} {path}"
            if a is None and b is None:
                continue
            if b is None:
                out.append(Change("breaking", where, "method removed"))
            elif a is None:
                out.append(Change("additive", where, "new method"))
            else:
                _operation(a, b, where, out, old, new)
    return out


def _operation(a: dict[str, Any], b: dict[str, Any], where: str, out: list[Change], ro: Any, rn: Any) -> None:
    pa = {(p.get("in"), p.get("name")): p for p in (_resolve(x, ro) for x in a.get("parameters") or [])}
    pb = {(p.get("in"), p.get("name")): p for p in (_resolve(x, rn) for x in b.get("parameters") or [])}
    for key in sorted(set(pa) | set(pb), key=str):
        name = f"{where} param {key[1]} ({key[0]})"
        if key not in pb:
            out.append(Change("breaking", name, "parameter removed"))
        elif key not in pa:
            kind = "breaking" if pb[key].get("required") else "additive"
            out.append(Change(kind, name, "new parameter"))
        else:
            if pb[key].get("required") and not pa[key].get("required"):
                out.append(Change("breaking", name, "parameter became required"))
            _schema(pa[key].get("schema", {}), pb[key].get("schema", {}), "request", name, out, ro, rn)
    ba, bb = _body(a), _body(b)
    if ba is not None or bb is not None:
        if bb is None:
            out.append(Change("breaking", f"{where} body", "request body removed"))
        elif ba is None:
            kind = "breaking" if (b.get("requestBody") or {}).get("required") else "additive"
            out.append(Change(kind, f"{where} body", "new request body"))
        else:
            _schema(ba, bb, "request", f"{where} body", out, ro, rn)
    ra, rb = a.get("responses") or {}, b.get("responses") or {}
    for code in sorted(c for c in ra if str(c).startswith("2")):
        if code not in rb:
            out.append(Change("breaking", f"{where} {code}", "success response removed"))
            continue
        sa, sb = _json(ra[code]), _json(rb[code])
        if sa is not None and sb is not None:
            _schema(sa, sb, "response", f"{where} {code}", out, ro, rn)


def _json(part: dict[str, Any]) -> Any:
    return ((part.get("content") or {}).get("application/json") or {}).get("schema")


def _body(op: dict[str, Any]) -> Any:
    rb = op.get("requestBody")
    return _json(rb) if isinstance(rb, dict) else None
