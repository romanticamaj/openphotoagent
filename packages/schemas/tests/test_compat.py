"""The compatibility checker behind the v1.0 contract snapshots (09b §9.1 FRZ-007; VERSIONING §4)."""

import copy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as hs
from opagent_schemas.compat import breaking, compare

BASE = {
    "type": "object",
    "required": ["id"],
    "properties": {
        "id": {"type": "string"},
        "size": {"type": "integer"},
        "kind": {"enum": ["a", "b"]},
        "meta": {"type": "object", "properties": {"x": {"type": "string"}}},
    },
}


def kinds(old, new, side):
    return sorted({c.kind for c in compare(old, new, side)})


@pytest.mark.tc("FRZ-007")
def test_frz_007_additive_vs_breaking():
    new = copy.deepcopy(BASE)
    new["properties"]["extra"] = {"type": "string"}  # a new optional field
    assert kinds(BASE, new, "request") == ["additive"] and kinds(BASE, new, "response") == ["additive"]
    req = copy.deepcopy(new)
    req["required"] = ["id", "extra"]  # …that is required on input
    assert breaking(compare(BASE, req, "request"))
    gone = copy.deepcopy(BASE)
    del gone["properties"]["size"]
    assert breaking(compare(BASE, gone, "request")) and breaking(compare(BASE, gone, "response"))
    typed = copy.deepcopy(BASE)
    typed["properties"]["size"] = {"type": "string"}
    assert breaking(compare(BASE, typed, "response"))
    nested = copy.deepcopy(BASE)
    del nested["properties"]["meta"]["properties"]["x"]
    c = compare(BASE, nested, "response")
    assert breaking(c) and c[0].path == "properties.meta.properties.x"
    wider = copy.deepcopy(BASE)
    wider["properties"]["kind"] = {"enum": ["a", "b", "c"]}
    assert not breaking(compare(BASE, wider, "request"))  # callers may send more
    assert not breaking(compare(BASE, wider, "response"))  # clients must ignore unknown values (VERSIONING §4)
    narrow = copy.deepcopy(BASE)
    narrow["properties"]["kind"] = {"enum": ["a"]}
    assert breaking(compare(BASE, narrow, "request"))
    optional = copy.deepcopy(BASE)
    optional["required"] = []
    assert breaking(compare(BASE, optional, "response")) and not breaking(compare(BASE, optional, "request"))
    assert compare(BASE, copy.deepcopy(BASE), "response") == []


@pytest.mark.tc("FRZ-007")
def test_frz_007_openapi_paths_and_refs():
    def spec(paths, comps):
        return {"openapi": "3.1.0", "paths": paths, "components": {"schemas": comps}}

    item = {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}
    ok = {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Item"}}}}}
    old = spec({"/a": {"get": {"responses": ok}}}, {"Item": item})
    new = spec({"/a": {"get": {"responses": ok}}, "/b": {"post": {"responses": ok}}}, {"Item": item})
    assert kinds(old, new, "api") == ["additive"]
    assert breaking(compare(new, old, "api"))  # a removed path
    item2 = copy.deepcopy(item)
    item2["properties"] = {}
    assert breaking(compare(old, spec({"/a": {"get": {"responses": ok}}}, {"Item": item2}), "api"))  # via $ref
    param = {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}
    withp = spec({"/a": {"get": {"responses": ok, "parameters": [param]}}}, {"Item": item})
    assert breaking(compare(old, withp, "api"))  # a new required parameter
    optp = spec({"/a": {"get": {"responses": ok, "parameters": [{**param, "required": False}]}}}, {"Item": item})
    assert not breaking(compare(old, optp, "api"))


def shuffled(d, seed):
    import random

    r = random.Random(seed)
    if isinstance(d, dict):
        ks = list(d)
        r.shuffle(ks)
        return {k: shuffled(d[k], seed) for k in ks}
    if isinstance(d, list) and all(isinstance(x, str) for x in d):
        out = list(d)
        r.shuffle(out)
        return out
    return d


@pytest.mark.tc("FRZ-007")
@settings(max_examples=200)
@given(hs.integers(0, 10_000))
def test_frz_007_order_does_not_matter(seed):
    assert compare(BASE, shuffled(BASE, seed), "request") == []
    assert compare(BASE, shuffled(BASE, seed), "response") == []
