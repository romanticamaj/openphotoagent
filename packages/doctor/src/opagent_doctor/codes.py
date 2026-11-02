"""Error-code catalogue → structured problems with ordered fixes (06 §4.2–§4.7).

``catalog.yaml`` is the single product source of severity, transient flag and fix order; every blocker/warning the
system emits is built here, so the PF-071 rules (≥ 1 fix, exactly one primary first, ``choose_alternative`` has a
consequence) hold everywhere by construction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CATALOG: dict[str, dict[str, Any]] = yaml.safe_load(
    (Path(__file__).with_name("catalog.yaml")).read_text(encoding="utf-8")
)


def _pick(f: dict[str, Any], deployment: str, os: str) -> dict[str, Any]:
    if "one_of" not in f:
        return dict(f)
    opts = f["one_of"]
    for o in opts:
        if o.get("deployment") in (deployment, os):
            return dict(o)
    plain = [o for o in opts if "deployment" not in o]
    return dict(plain[0] if plain else opts[-1])


def fixes_for(code: str, deployment: str = "desktop", os: str = "windows", **payloads: Any) -> list[dict[str, Any]]:
    entry = CATALOG.get(code) or {"fixes": [{"type": "open_docs", "target": "troubleshooting"}]}
    out = []
    for i, raw in enumerate(entry["fixes"]):
        f = _pick(raw, deployment, os)
        target = str(f.get("target") or f["type"])
        fid = target.split("(")[0].strip().replace(" ", "_").replace("-", "_")
        fx: dict[str, Any] = {
            "id": fid,
            "fix_id": f"{code}.{fid}",
            "type": f["type"],
            "kind": f["type"],  # legacy name kept for v0.1 clients
            "target": f.get("target"),
            "primary": i == 0,
            "label_key": f"fix.{fid}",
        }
        if f["type"] == "choose_alternative":
            fx["consequence"] = {"key": f"fix.{fid}.consequence"}
        if fid in payloads:
            fx["payload"] = payloads[fid]
        out.append(fx)
    return out


def problem(
    code: str,
    params: dict[str, Any] | None = None,
    deployment: str = "desktop",
    os: str = "windows",
    details: dict[str, Any] | None = None,
    subject: dict[str, Any] | None = None,
    **payloads: Any,
) -> dict[str, Any]:
    entry = CATALOG.get(code, {"severity": "blocker", "transient": False})
    return {
        "code": code,
        "severity": entry["severity"],
        "retryable": bool(entry.get("transient")),
        "title_key": f"errors.{code}.title",
        "message_key": f"errors.{code}.message",
        "params": params or {},
        "details": details or {},
        "subject": subject,
        "fixes": fixes_for(code, deployment, os, **payloads),
    }
