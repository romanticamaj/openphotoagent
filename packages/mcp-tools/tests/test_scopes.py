"""MCP-SCOPE-001 (pure gate, all 324 sub-cases) and MCP-SCOPE-007 (09b §3.2; ADR-0009).

The matrix below is transcribed from 09b §3.2. A ✓ cell may carry a workspace switch that must also be on;
those cells are checked with the switch on and off.
"""

import itertools

import jsonschema
import pytest
from opagent_mcp.scopes import ALL_SCOPES, Settings, allowed, visible
from opagent_mcp.tools import BY_NAME, SERVER_VERSION, TOOLS

READ = [
    "search_photos",
    "get_photo",
    "find_similar",
    "find_duplicates",
    "list_tags",
    "list_categories",
    "list_facets",
    "list_folders",
    "list_collections",
    "get_job_status",
    "get_organize_plan",
    "count_photos",
]
MATRIX = {
    **{t: "photos:read" for t in READ},
    **{t: "photos:describe" for t in ("describe_photo", "verify_photos", "compare_photos")},
    "update_labels": "labels:write",
    **{t: "collections:write" for t in ("create_collection", "add_to_collection", "remove_from_collection")},
    **{t: "jobs:run" for t in ("run_labeling_job", "cancel_job")},
    "propose_organize": "organize:propose",
    **{t: "organize:apply" for t in ("apply_organize", "undo_organize")},
    **{t: "faces:read" for t in ("list_people", "get_person", "find_photos_of_person")},
}
SWITCH = {
    "apply_organize": "allow_apply_requests",
    "undo_organize": "allow_apply_requests",
    "list_people": "faces_enabled",
    "get_person": "faces_enabled",
    "find_photos_of_person": "faces_enabled",
}
TOKENS = [(s, frozenset({s})) for s in ALL_SCOPES] + [("all", frozenset(ALL_SCOPES)), ("none", frozenset())]
ON = Settings(allow_apply_requests=True, faces_enabled=True)
OFF = Settings()


@pytest.mark.contract
@pytest.mark.tc("MCP-SCOPE-001")
def test_matrix_covers_exactly_27_tools_and_10_scopes():
    assert len(ALL_SCOPES) == 10 and sorted(MATRIX) == sorted(BY_NAME) and len(TOOLS) == 27
    assert {t.name: t.scope for t in TOOLS} == MATRIX


@pytest.mark.contract
@pytest.mark.tc("MCP-SCOPE-001")
@pytest.mark.parametrize(("tool", "token"), list(itertools.product(sorted(MATRIX), TOKENS)), ids=str)
def test_scope_gate(tool, token):
    label, scopes = token
    want = MATRIX[tool] in scopes
    ok, reason = allowed(BY_NAME[tool], scopes, ON, version="9.9.9")
    assert ok is want and (reason is None if want else reason == "insufficient_scope")
    if tool in SWITCH and want:  # the cell's extra condition: the workspace switch must be on too
        ok, reason = allowed(BY_NAME[tool], scopes, OFF, version="9.9.9")
        assert not ok and reason == "setting_disabled"
    if label == "photos:media":  # media only adds to read; alone it opens nothing
        assert not ok


@pytest.mark.contract
@pytest.mark.tc("MCP-SCOPE-001")
def test_version_gate_and_visible_order():
    every = frozenset(ALL_SCOPES)
    later = [t.name for t in TOOLS if t.available_from > SERVER_VERSION]
    assert sorted(later) == sorted(
        [
            "describe_photo",
            "create_collection",
            "add_to_collection",
            "remove_from_collection",
            "list_collections",
            "count_photos",
            "verify_photos",
            "compare_photos",
        ]
    )
    for name in later:
        assert allowed(BY_NAME[name], every, ON) == (False, "not_available")
    names = [t.name for t in visible(every, ON)]
    assert names == sorted(names) and not set(names) & set(later) and len(names) == 19
    assert [t.name for t in visible(frozenset({"photos:read"}), OFF)] == sorted(t for t in READ if t not in later)
    assert visible(frozenset(), ON) == []


@pytest.mark.contract
@pytest.mark.tc("MCP-SCOPE-001", "MCP-TOOL-001")
def test_definitions_are_closed_2020_12_schemas():
    for t in TOOLS:
        d = t.definition()
        for key in ("inputSchema", "outputSchema"):
            jsonschema.Draft202012Validator.check_schema(d[key])
        assert d["inputSchema"]["additionalProperties"] is False
        assert d["annotations"].get("openWorldHint") is False


@pytest.mark.security
@pytest.mark.tc("MCP-SCOPE-007")
def test_scope_007_no_capability_for_forbidden_things():
    names = " ".join(BY_NAME)
    for word in ("delete", "exif_write", "add_root", "root", "upload", "embedding", "rename_person", "merge"):
        assert word not in names
    smuggled = [
        ("get_photo", {"id": "a", "delete": True}),
        ("update_labels", {"id": "a", "exif": {"Artist": "x"}}),
        ("list_folders", {"add_root": "/home"}),
        ("find_similar", {"id": "a", "image_base64": "AAAA"}),
        ("find_photos_of_person", {"person_ids": ["p"], "reference_face": "AAAA"}),
        ("get_photo", {"id": "a", "include": ["embedding"]}),
        ("update_labels", {"id": "a", "person_name": "x"}),
    ]
    for name, args in smuggled:
        v = jsonschema.Draft202012Validator(BY_NAME[name].definition()["inputSchema"])
        assert list(v.iter_errors(args)), (name, args)
