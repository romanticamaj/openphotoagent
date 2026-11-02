"""MCP-TOOL (09b §3.1): published definitions match the snapshots byte for byte; 200 valid generated inputs per
tool get a result that matches ``outputSchema``; 50 invalid ones get a tool error with a code, never a 500; and the
per-tool extra assertions."""

import json
from pathlib import Path

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as hs
from hypothesis_jsonschema import from_schema
from opagent_mcp.tools import BY_NAME, SERVER_VERSION, TOOLS
from opagent_testing.mcp_harness import M

SNAP = Path(__file__).resolve().parents[4] / "tests/contract/mcp/snapshots"
V02 = [t.name for t in TOOLS if t.available_from <= SERVER_VERSION]
TOOL_IDS = {
    "search_photos": "MCP-TOOL-001",
    "get_photo": "MCP-TOOL-002",
    "find_similar": "MCP-TOOL-003",
    "find_duplicates": "MCP-TOOL-004",
    "list_tags": "MCP-TOOL-005",
    "list_categories": "MCP-TOOL-006",
    "list_facets": "MCP-TOOL-007",
    "list_folders": "MCP-TOOL-008",
    "describe_photo": "MCP-TOOL-009",
    "update_labels": "MCP-TOOL-010",
    "create_collection": "MCP-TOOL-011",
    "add_to_collection": "MCP-TOOL-012",
    "remove_from_collection": "MCP-TOOL-013",
    "list_collections": "MCP-TOOL-014",
    "run_labeling_job": "MCP-TOOL-015",
    "get_job_status": "MCP-TOOL-016",
    "cancel_job": "MCP-TOOL-017",
    "propose_organize": "MCP-TOOL-018",
    "get_organize_plan": "MCP-TOOL-019",
    "apply_organize": "MCP-TOOL-020",
    "undo_organize": "MCP-TOOL-021",
    "list_people": "MCP-TOOL-022",
    "get_person": "MCP-TOOL-023",
    "find_photos_of_person": "MCP-TOOL-024",
    "count_photos": "MCP-TOOL-025",
    "verify_photos": "MCP-TOOL-026",
    "compare_photos": "MCP-TOOL-027",
}
ALL = ["photos:read", "photos:media", "labels:write", "jobs:run", "organize:propose", "faces:read", "faces:media"]


@pytest.mark.contract
@pytest.mark.tc(*TOOL_IDS.values())
@pytest.mark.parametrize("tool", sorted(BY_NAME))
def test_definition_matches_snapshot(tool):
    want = (SNAP / f"{tool}.json").read_text(encoding="utf-8")
    got = json.dumps(BY_NAME[tool].definition(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert got == want, f"definition of {tool} changed: run tools/gen_mcp_snapshots.py and explain it in the PR"


@pytest.fixture
def fresh(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path)
    x.aids = [x.add(f"2025/{i}.jpg", caption="貓 海邊", tags=("animal.cat", "scene.beach")) for i in range(6)]
    x.index()
    x.settings(allow_apply_requests=True, faces_enabled=True, media_policy="thumb_256")
    x.pat = x.token(ALL)
    x.sql("update mcp_token set scopes = array_append(scopes, 'organize:apply') where id = :i", i=x.last_token["id"])
    yield x
    x.eng.dispose()


@pytest.fixture(scope="module")
def lib(embedded_pg_cluster, tmp_path_factory):
    from opagent_testing import pg as _pg

    db = _pg.clone(embedded_pg_cluster)
    from opagent_testing.clock import FakeClock

    x = M(db.dsn, tmp_path_factory.mktemp("mcp"), clock=FakeClock())
    x.aids = [x.add(f"2025/{i}.jpg", caption="貓 海邊", tags=("animal.cat", "scene.beach")) for i in range(6)]
    x.index()
    x.settings(allow_apply_requests=True, faces_enabled=True, media_policy="thumb_256")
    x.pat = x.token(ALL)
    x.sql("update mcp_token set scopes = array_append(scopes, 'organize:apply') where id = :i", i=x.last_token["id"])
    yield x
    x.eng.dispose()
    _pg.drop(embedded_pg_cluster, db)


def _tick(lib, tool):
    from opagent_mcp.ratelimit import LIMITS, kind_for

    lim = LIMITS[kind_for(tool)]  # call no faster than the tool's limit: the limiter is not what is under test here
    lib.client.app.state.opa.clock.advance(max(1.0, lim.window_s / lim.per_window))


def _check(lib, tool, args, valid):
    _tick(lib, tool)
    r = lib.rpc(lib.pat, "tools/call", {"name": tool, "arguments": args})
    assert r.status_code == 200, r.text
    out = r.json()
    assert "result" in out, out
    res = out["result"]
    if res["isError"]:
        sc = res["structuredContent"]
        assert sc["code"] and isinstance(sc["code"], str)
        assert all("fix_id" not in f and "id" not in f for f in sc.get("fixes", []))
        if not valid:
            return
        assert sc["code"] not in ("INVALID_ARGUMENT", "INTERNAL"), (tool, args, sc)
    else:
        assert valid, (tool, args)
        jsonschema.Draft202012Validator(BY_NAME[tool].definition()["outputSchema"]).validate(res["structuredContent"])


@pytest.mark.contract
@pytest.mark.tc(*(TOOL_IDS[t] for t in V02))
@pytest.mark.parametrize("tool", V02)
def test_generated_inputs(lib, tool):
    schema = BY_NAME[tool].definition()["inputSchema"]

    @settings(max_examples=200, deadline=None, suppress_health_check=list(HealthCheck), derandomize=True)
    @given(from_schema(schema))
    def valid(args):
        _check(lib, tool, args, True)

    @settings(max_examples=50, deadline=None, suppress_health_check=list(HealthCheck), derandomize=True)
    @given(
        hs.dictionaries(
            hs.sampled_from(["zz_unknown", "id", "limit", "k", "plan_id"]),
            hs.one_of(hs.integers(-5, 10**6), hs.text(max_size=3), hs.none()),
            min_size=1,
        )
    )
    def invalid(args):
        if not list(jsonschema.Draft202012Validator(schema).iter_errors(args)):
            return
        _tick(lib, tool)
        out = lib.rpc(lib.pat, "tools/call", {"name": tool, "arguments": args}).json()["result"]
        assert out["isError"] and out["structuredContent"]["code"] == "INVALID_ARGUMENT"

    valid()
    invalid()


@pytest.mark.contract
@pytest.mark.tc("MCP-TOOL-001")
def test_tool_001_search_extras(fresh):
    lib = fresh
    d = BY_NAME["search_photos"].definition()["inputSchema"]["properties"]
    assert {"semantic_text_en", "return_selection"} <= set(d)
    out = lib.ok(lib.pat, "search_photos", {"query": "貓", "limit": 2, "return_selection": True})
    assert out["selection_id"].startswith("sel_") and out["next_cursor"].startswith("cur_")
    nxt = lib.ok(lib.pat, "search_photos", {"cursor": out["next_cursor"]})
    assert not {r["id"] for r in nxt["results"]} & {r["id"] for r in out["results"]}


@pytest.mark.contract
@pytest.mark.tc("MCP-TOOL-002", "MCP-TOOL-003", "MCP-TOOL-024")
def test_tools_take_no_image_input():
    for name in ("find_similar", "find_photos_of_person", "search_photos", "get_photo"):
        props = json.dumps(BY_NAME[name].definition()["inputSchema"])
        for word in ("base64", "image_data", "upload", '"image"'):
            assert word not in props, (name, word)
    fs = BY_NAME["find_similar"].definition()["inputSchema"]["properties"]
    assert (fs["k"]["minimum"], fs["k"]["maximum"], fs["thumbnails"]["maximum"]) == (1, 50, 8)
    fp = BY_NAME["find_photos_of_person"].definition()["inputSchema"]["properties"]["person_ids"]
    assert (fp["minItems"], fp["maxItems"]) == (1, 5)


@pytest.mark.contract
@pytest.mark.tc("MCP-TOOL-004", "MCP-TOOL-005", "MCP-TOOL-019")
def test_bounds():
    p = BY_NAME["find_duplicates"].definition()["inputSchema"]["properties"]
    assert p["kind"]["enum"] == ["exact", "near", "both"] and p["limit"]["maximum"] == 50
    assert BY_NAME["list_tags"].definition()["inputSchema"]["properties"]["limit"]["maximum"] == 500
    g = BY_NAME["get_organize_plan"].definition()["inputSchema"]["properties"]
    assert g["limit"]["maximum"] == 200 and "conflicts_only" in g


@pytest.mark.contract
@pytest.mark.tc("MCP-TOOL-006", "MCP-TOOL-007", "MCP-TOOL-008", "MCP-TOOL-016")
def test_read_tool_outputs(fresh):
    lib = fresh
    assert lib.ok(lib.pat, "list_categories", {})["label_set_version"].startswith("vocab-")
    f = lib.ok(lib.pat, "list_facets", {"fields": ["year"], "filters": {"tags_any": ["animal.cat"]}})
    assert f["facets"]["year"][0]["count"] == 6
    for folder in lib.ok(lib.pat, "list_folders", {})["folders"]:
        text = folder["path"]["text"] if folder["path"] else ""
        assert not text.startswith("/") and ":" not in text
    job = lib.ok(lib.pat, "run_labeling_job", {"scope": {"photo_ids": lib.aids[:1]}, "only_unlabeled": False})
    st = lib.ok(lib.pat, "get_job_status", {"job_id": job["job_id"]})
    assert {"state", "p50_s"} <= set(st["eta"]) and "waiting_reason" in st


@pytest.mark.contract
@pytest.mark.tc("MCP-TOOL-010", "MCP-TOOL-015", "MCP-TOOL-017")
def test_write_tool_rules(fresh):
    lib = fresh
    sel = lib.ok(lib.pat, "search_photos", {"query": "貓", "return_selection": True})["selection_id"]
    assert lib.ok(lib.pat, "update_labels", {"selection_id": sel, "add_tags": ["夕陽"]})["updated"] == 6
    pr = BY_NAME["run_labeling_job"].definition()["inputSchema"]["properties"]["priority"]
    assert pr["enum"] == ["normal", "low"]
    job = lib.ok(lib.pat, "run_labeling_job", {"scope": {"selection_id": sel}, "only_unlabeled": False})
    assert job["mode"] == "dry_run" and job["status"] == "running"  # no worker in this test: it waits
    assert lib.sql("select mode from job where id=:j", j=job["job_id"]).scalar() == "dry_run"
    assert (
        lib.sql(
            "select count(*) from output_plan p join job j on j.id=p.job_id where j.id=:j", j=job["job_id"]
        ).scalar()
        == 0
    )
    ui_job = lib.sql(
        "insert into job(id, workspace_id, kind, status, params) values ('ui-job','default','label',"
        "'running','{}') returning id"
    ).scalar()
    out = lib.call(lib.pat, "cancel_job", {"job_id": ui_job})["result"]
    assert out["isError"] and out["structuredContent"]["code"] == "NOT_FOUND"
    assert lib.ok(lib.pat, "cancel_job", {"job_id": job["job_id"]})["status"] in ("cancelling", "cancelled")


@pytest.mark.contract
@pytest.mark.tc("MCP-TOOL-018", "MCP-TOOL-020", "MCP-TOOL-021")
def test_organize_tool_rules(fresh):
    lib = fresh
    d = BY_NAME["propose_organize"].definition()
    assert "trash" in d["inputSchema"]["properties"]["action"]["enum"]
    assert d["inputSchema"]["properties"]["path_template"]["maxLength"] == 200
    for n in ("apply_organize", "undo_organize"):
        assert BY_NAME[n].definition()["annotations"]["destructiveHint"] is True
    for i in range(25):
        lib.add(f"2025/more/{i}.jpg", caption="海邊", tags=("scene.beach",))
    lib.index()
    out = lib.ok(
        lib.pat,
        "propose_organize",
        {"scope": {"filters": {}}, "action": "copy", "target": {"source": "sorted"}, "path_template": "{stem}{ext}"},
    )
    assert out["plan_id"] and out["approval_required"] is True and len(out["sample_actions"]) == 20
    for _ in range(3):
        st = lib.ok(lib.pat, "apply_organize", {"plan_id": out["plan_id"]})["status"]
        assert st in ("input_required", "approval_pending")  # never "applying" without a person
    assert lib.sql("select count(*) from worker_task where kind='apply'").scalar() == 0


@pytest.mark.contract
@pytest.mark.tc("MCP-TOOL-022", "MCP-TOOL-023")
def test_people_tools_before_faces(fresh):
    lib = fresh
    assert lib.ok(lib.pat, "list_people", {})["people"] == []  # nobody is named yet; unnamed groups never listed
    out = lib.call(lib.pat, "get_person", {"person_id": "p1"})["result"]
    assert out["isError"] and not [c for c in out["content"] if c["type"] == "image"]
