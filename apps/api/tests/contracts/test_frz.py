"""v1.0 interface freeze (VERSIONING §4; 09b §9.1 FRZ-001..006): the code still produces exactly the snapshotted
contracts in ``contracts/``, and the checker would catch a breaking change in each of them."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from opagent_schemas.compat import breaking, compare

ROOT = Path(__file__).resolve().parents[4]
_spec = importlib.util.spec_from_file_location("contracts_tool", ROOT / "tools/contracts.py")
contracts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contracts)


@pytest.fixture(scope="module")
def current():
    return contracts.generate()


def unchanged(current, name):
    changes = contracts.check(current)[name]
    assert changes == [], (
        "contract drifted — "
        + "; ".join(f"{c.kind} {c.path}: {c.message}" for c in changes)
        + (
            " (additive changes: run `just contracts-update`; breaking ones need a new major and a CHANGELOG migration note)"
        )
    )


@pytest.mark.contract
@pytest.mark.tc("FRZ-001")
def test_frz_001_rest_v1(current):
    unchanged(current, "openapi-v1")
    spec = current["openapi-v1"]
    assert "version" not in spec["info"] and all(
        p.startswith(("/api/v1", "/mcp", "/oauth", "/.well-known", "/auth", "/healthz", "/metrics"))
        for p in spec["paths"]
    )
    gone = copy.deepcopy(spec)
    gone["paths"].pop("/api/v1/search")
    assert breaking(compare(spec, gone, "api"))
    more = copy.deepcopy(spec)
    more["paths"]["/api/v1/new-thing"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    assert not breaking(compare(spec, more, "api")) and compare(spec, more, "api")


@pytest.mark.contract
@pytest.mark.tc("FRZ-002")
def test_frz_002_worker_protocol(current):
    from opagent_api.routes_remote import PROTOCOL
    from opagent_worker.remote import PROTOCOL_VERSION

    unchanged(current, "worker-protocol")
    doc = current["worker-protocol"]
    assert doc["x-protocol-version"] == PROTOCOL == PROTOCOL_VERSION  # both ends speak the frozen version
    assert all(p.startswith("/worker-api/v1") for p in doc["paths"]) and "/worker-api/v1/heartbeat" in doc["paths"]
    # a breaking change without a major bump is caught: e.g. a new required field in a worker message
    hb = copy.deepcopy(doc)
    ref = hb["paths"]["/worker-api/v1/heartbeat"]["post"]["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    name = ref.rsplit("/", 1)[1]
    hb["components"]["schemas"][name]["properties"]["gpu_serial"] = {"type": "string"}
    hb["components"]["schemas"][name]["required"].append("gpu_serial")
    assert breaking(compare(doc, hb, "api"))
    kinds = copy.deepcopy(doc)
    kinds["x-rpc-kinds"] = [k for k in kinds["x-rpc-kinds"] if k != "scan"]
    assert contracts.dump(kinds) != contracts.dump(doc)


@pytest.mark.contract
@pytest.mark.tc("FRZ-003")
def test_frz_003_mcp_manifest(current):
    unchanged(current, "mcp-manifest")
    m = current["mcp-manifest"]
    assert m["x-manifest-version"] == "1.0.0" and len(m["properties"]) >= 27
    assert "apply_organize" in m["properties"] and m["properties"]["apply_organize"]["x-scope"] == "organize:apply"
    removed = copy.deepcopy(m)
    del removed["properties"]["search_photos"]
    assert breaking(compare(m, removed, "request"))
    req = copy.deepcopy(m)
    req["properties"]["search_photos"]["required"] = [*req["properties"]["search_photos"].get("required", []), "limit"]
    assert breaking(compare(m, req, "request"))
    opt = copy.deepcopy(m)
    opt["properties"]["search_photos"]["properties"]["new_option"] = {"type": "boolean"}
    assert not breaking(compare(m, opt, "request"))


@pytest.mark.contract
@pytest.mark.tc("FRZ-004")
def test_frz_004_pack_api(current):
    from opagent_worker.packs import PACK_API

    unchanged(current, "pack-api")
    assert current["pack-api"]["x-pack-api"] == PACK_API == 1
    gone = copy.deepcopy(current["pack-api"])
    del gone["properties"]["index_entry"]["properties"]["files_manifest"]
    assert breaking(compare(current["pack-api"], gone, "response"))


@pytest.mark.contract
@pytest.mark.tc("FRZ-005")
def test_frz_005_registry_from_v0_1(current, tmp_path):
    from opagent_registry.loader import load_registry

    unchanged(current, "registry-schema")
    old = ROOT / "fixtures/compat/registry-v0.1"
    reg = load_registry(old)
    files = sorted((old / "models").glob("*.yaml"))
    assert files and len(reg.models) == len(files)
    for f in files:
        raw = yaml.safe_load(f.read_text())
        spec = reg.models[raw["id"]].spec
        assert spec.schema_version == raw.get("schema_version", 1)
        assert sorted(v.id for v in spec.variants) == sorted(v["id"] for v in raw["variants"])  # content kept


@pytest.mark.contract
@pytest.mark.tc("FRZ-006")
def test_frz_006_presets_from_v0_1_and_v0_3(current):
    from opagent_core.preset_io import import_text

    unchanged(current, "preset-schema")
    for name in ("by-date", "by-content-12"):
        v1 = import_text((ROOT / f"fixtures/compat/presets-v0.1/{name}.yaml").read_text())
        v2 = import_text((ROOT / f"fixtures/compat/presets-v0.3/{name}.yaml").read_text())
        assert v1["migrated_from"] == 1 and "migrated_from" not in v2
        raw = yaml.safe_load((ROOT / f"fixtures/compat/presets-v0.1/{name}.yaml").read_text())
        assert v1["preset"]["key"] == raw["key"] == name
        assert v1["preset"].get("output_rules") == raw.get("output_rules")
        assert json.dumps(v2["preset"], sort_keys=True) == json.dumps(
            yaml.safe_load((ROOT / f"fixtures/compat/presets-v0.3/{name}.yaml").read_text())["preset"], sort_keys=True
        )
