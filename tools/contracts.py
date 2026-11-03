"""Interface contract snapshots for v1.0 (VERSIONING §4; 09b §9.1 FRZ-001..006).

    uv run python tools/contracts.py            # check: exit 1 and list differences when the code drifted
    uv run python tools/contracts.py --update   # write contracts/*.json (only for additive changes, or a new major)

Each contract is generated from the code itself — the FastAPI app, the worker's RPC table, the MCP tool table, the
pack and registry models, the preset format — so a snapshot can only go stale by a code change, and the comparison
(``opagent_schemas.compat``) says whether that change is additive or breaking. ``info.version`` (the product version)
is not part of any contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "contracts"
NAMES = ("openapi-v1", "worker-protocol", "mcp-manifest", "pack-api", "registry-schema", "preset-schema")
SIDE = {  # how compat reads each contract
    "openapi-v1": "api",
    "worker-protocol": "api",
    "mcp-manifest": "request",  # tool inputs are requests
    "pack-api": "response",  # the worker reads the index
    "registry-schema": "request",  # the app reads registry YAML
    "preset-schema": "request",  # the app imports preset files
}


def _app() -> Any:
    from unittest import mock

    from opagent_api.app import create_app

    with (
        mock.patch("opagent_api.faces.install_roles"),
        mock.patch("opagent_api.faces.api_role_hook"),
        mock.patch("opagent_api.state.AppState.ensure_workspace"),
        mock.patch("opagent_api.multiuser.bootstrap_owner"),
    ):
        # the multi-user control plane serves a superset of the desktop app's routes
        return create_app("postgresql://contract@localhost/contract", multi_user=True, jwt_secret="c" * 40)


def _openapi() -> dict[str, Any]:
    spec = _app().openapi()
    spec = json.loads(json.dumps(spec))
    spec.get("info", {}).pop("version", None)
    return spec


def openapi_v1(spec: dict[str, Any]) -> dict[str, Any]:
    paths = {p: v for p, v in spec["paths"].items() if not p.startswith("/worker-api")}
    return {**spec, "paths": paths}


def worker_protocol(spec: dict[str, Any]) -> dict[str, Any]:
    from opagent_api.routes_remote import PROTOCOL
    from opagent_worker.agent import RPC_KINDS

    paths = {p: v for p, v in spec["paths"].items() if p.startswith("/worker-api")}
    return {
        "openapi": spec["openapi"],
        "info": {"title": "OpenPhotoAgent worker protocol"},
        "x-protocol-version": PROTOCOL,
        "x-welcome": {
            "fields": ["egress_policy", "min_protocol", "protocol", "status", "type", "workspaces"],
            "status": ["ok", "server_too_old", "upgrade_required"],
            "ws_messages": {"worker": ["ack", "hello", "ping"], "cp": ["pong", "welcome", "work.available"]},
        },
        "x-rpc-kinds": sorted(RPC_KINDS),
        "paths": paths,
        "components": spec.get("components", {}),
    }


def mcp_manifest() -> dict[str, Any]:
    from opagent_mcp.tools import MANIFEST_VERSION, TOOLS

    props = {}
    for t in sorted(TOOLS, key=lambda t: t.name):
        d = t.definition()
        props[t.name] = {**d["inputSchema"], "x-scope": t.scope, "x-available-from": t.available_from}
    return {
        "x-manifest-version": MANIFEST_VERSION,
        "type": "object",
        "required": sorted(props),
        "properties": props,
    }


def pack_api() -> dict[str, Any]:
    from opagent_worker.packs import INDEX_ENTRY_SCHEMA, LOCK_ENTRY_SCHEMA, PACK_API

    return {
        "x-pack-api": PACK_API,
        "type": "object",
        "required": ["index_entry", "lock_entry"],
        "properties": {"index_entry": INDEX_ENTRY_SCHEMA, "lock_entry": LOCK_ENTRY_SCHEMA},
    }


def registry_schema() -> dict[str, Any]:
    from opagent_registry.schema import GpuClasses, ModelSpec, PinsLock, RuntimePack

    out = {}
    for name, model in (
        ("model", ModelSpec),
        ("runtime_pack", RuntimePack),
        ("gpu_classes", GpuClasses),
        ("pins", PinsLock),
    ):
        out[name] = model.model_json_schema(ref_template="#/properties/" + name + "/$defs/{model}")
    return {"type": "object", "required": sorted(out), "properties": out}


def preset_schema() -> dict[str, Any]:
    from opagent_core.preset_io import PRESET_SCHEMA

    return PRESET_SCHEMA


def generate() -> dict[str, dict[str, Any]]:
    spec = _openapi()
    return {
        "openapi-v1": openapi_v1(spec),
        "worker-protocol": worker_protocol(spec),
        "mcp-manifest": mcp_manifest(),
        "pack-api": pack_api(),
        "registry-schema": registry_schema(),
        "preset-schema": preset_schema(),
    }


def load(name: str) -> dict[str, Any]:
    return json.loads((DIR / f"{name}.json").read_text())  # type: ignore[no-any-return]


def dump(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def check(current: dict[str, dict[str, Any]] | None = None) -> dict[str, list[Any]]:
    """name → changes (empty when the snapshot matches exactly)."""
    from opagent_schemas.compat import Change, compare

    current = current or generate()
    out: dict[str, list[Any]] = {}
    for name in NAMES:
        old, new = load(name), current[name]
        if dump(old) == dump(new):
            out[name] = []
            continue
        changes = compare(old, new, SIDE[name])
        version_keys = [k for k in old if k.startswith("x-") and old.get(k) != new.get(k)]
        for k in version_keys:
            changes.append(Change("additive", k, f"{old.get(k)} → {new.get(k)}"))
        out[name] = changes or [Change("additive", "", "documentation or metadata changed")]
    return out


def main(argv: list[str]) -> int:
    cur = generate()
    if "--update" in argv:
        DIR.mkdir(exist_ok=True)
        for name, doc in cur.items():
            (DIR / f"{name}.json").write_text(dump(doc))
        print(f"wrote {len(cur)} contracts to {DIR}")
        return 0
    bad = 0
    for name, changes in check(cur).items():
        for c in changes:
            bad += 1
            print(f"{name}: {c.kind.upper()} {c.path} — {c.message}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
