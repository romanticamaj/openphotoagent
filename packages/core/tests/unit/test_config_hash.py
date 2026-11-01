import copy
import json
import math
import random
import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st
from opagent_core.config_hash import ConfigError, canonical_json, config_hash, model_fingerprint

BASE = {
    "task_type": "classify",
    "model_version": "qwen3-vl-8b@q4_k_m",
    "prompt_template_ref": "builtin:classify_v1",
    "generation_params": {"temperature": 0.0, "max_tokens": 256, "top_p": 0.9},
    "label_set": {
        "key": "family-photo-categories",
        "version": 3,
        "labels": [{"key": "food", "display": "美食", "description": "餐點與飲料"}],
    },
    "structured_output_schema": {"type": "object"},
    "thresholds": {"accept": 0.80, "review": 0.50},
    "output_rule": {"path_template": "{category}/{stem}{ext}", "target_id": "t1", "conflict_policy": "rename"},
}


@pytest.mark.unit
@pytest.mark.tc("CORE-HASH-001")
def test_core_hash_001_key_order_irrelevant():
    a = copy.deepcopy(BASE)
    b = copy.deepcopy(BASE)
    b["generation_params"] = {"top_p": 0.9, "max_tokens": 256, "temperature": 0.0}
    b = dict(reversed(list(b.items())))
    assert config_hash(a) == config_hash(b)
    assert len(config_hash(a)) == 64
    assert canonical_json({"b": 1, "a": [1, {"d": 2, "c": "x"}]}) == '{"a":[1,{"c":"x","d":2}],"b":1}'


json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(-(10**6), 10**6)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=8),
    lambda ch: st.lists(ch, max_size=4) | st.dictionaries(st.text(max_size=5), ch, max_size=4),
    max_leaves=20,
)


def _nfc(v):
    if isinstance(v, str):
        return unicodedata.normalize("NFC", v)
    if isinstance(v, dict):
        return {unicodedata.normalize("NFC", k): _nfc(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_nfc(x) for x in v]
    return v


def _shuffle(v, rnd):
    if isinstance(v, dict):
        items = list(v.items())
        rnd.shuffle(items)
        return {k: _shuffle(x, rnd) for k, x in items}
    if isinstance(v, list):
        return [_shuffle(x, rnd) for x in v]
    return v


@pytest.mark.property
@pytest.mark.tc("CORE-HASH-002")
@given(v=json_values, seed=st.integers(0, 1000))
def test_core_hash_002_canonical_stable_under_reordering_and_whitespace(v, seed):
    rnd = random.Random(seed)
    reparsed = json.loads(json.dumps(_shuffle(v, rnd), indent=rnd.choice([None, 2, 4])))
    assert canonical_json(reparsed) == canonical_json(v)
    assert json.loads(canonical_json(v)) == _nfc(v)  # lossless modulo NFC (CORE-HASH-005)


@pytest.mark.property
@pytest.mark.tc("CORE-HASH-002")
@given(v=st.dictionaries(st.text(max_size=5), st.integers(), min_size=1, max_size=5))
def test_core_hash_002_leaf_change_changes_hash(v):
    k = next(iter(v))
    w = dict(v)
    w[k] = v[k] + 1
    assert config_hash({"generation_params": v}) != config_hash({"generation_params": w})


@pytest.mark.unit
@pytest.mark.tc("CORE-HASH-003")
def test_core_hash_003_numeric_normalization():
    assert canonical_json({"x": 1}) == canonical_json({"x": 1.0})
    assert canonical_json({"x": 0.1}) == canonical_json({"x": float("0.1000000000000000055")})
    assert canonical_json({"x": 0.1}) == '{"x":0.1}'


@pytest.mark.unit
@pytest.mark.tc("CORE-HASH-004")
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_core_hash_004_non_finite_rejected(bad):
    cfg = copy.deepcopy(BASE)
    cfg["generation_params"]["temperature"] = bad
    with pytest.raises(ConfigError) as e:
        config_hash(cfg)
    assert e.value.code == "CONFIG_NON_FINITE"


@pytest.mark.unit
@pytest.mark.tc("CORE-HASH-005")
def test_core_hash_005_unicode_nfc():
    a, b = copy.deepcopy(BASE), copy.deepcopy(BASE)
    a["label_set"]["labels"][0]["description"] = unicodedata.normalize("NFC", "Café 美食")
    b["label_set"]["labels"][0]["description"] = unicodedata.normalize("NFD", "Café 美食")
    assert config_hash(a) == config_hash(b) != ""


@pytest.mark.unit
@pytest.mark.tc("CORE-HASH-006")
@pytest.mark.parametrize(
    "path",
    [
        ("prompt_template_ref",),
        ("generation_params", "max_tokens"),
        ("label_set", "version"),
        ("label_set", "labels", 0, "description"),
        ("structured_output_schema", "type"),
    ],
)
def test_core_hash_006_inference_fields_invalidate(path):
    cfg = copy.deepcopy(BASE)
    node = cfg
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = f"changed-{node[path[-1]]}"
    assert config_hash(cfg) != config_hash(BASE)


@pytest.mark.unit
@pytest.mark.tc("CORE-HASH-007")
def test_core_hash_007_thresholds_do_not_change_hash():
    cfg = copy.deepcopy(BASE)
    cfg["thresholds"] = {"accept": 0.70, "review": 0.40}
    assert config_hash(cfg) == config_hash(BASE)
    assert config_hash(BASE)  # non-empty


@pytest.mark.unit
@pytest.mark.tc("CORE-HASH-008")
def test_core_hash_008_output_rule_does_not_change_hash():
    cfg = copy.deepcopy(BASE)
    cfg["output_rule"] = {"path_template": "{taken_at:%Y}/{stem}{ext}", "target_id": "t2", "conflict_policy": "skip"}
    assert config_hash(cfg) == config_hash(BASE) != ""


FP = {
    "adapter_key": "llamacpp-chat",
    "adapter_version": "1.2.0",
    "weights_sha256": "ab" * 32,
    "numerics_epoch": 3,
    "params": {"ctx": 8192},
}


@pytest.mark.unit
@pytest.mark.tc("CORE-HASH-009")
def test_core_hash_009_pack_version_does_not_change_fingerprint():
    a = model_fingerprint(**FP, pack_version="b7001-1")
    b = model_fingerprint(**FP, pack_version="b7002-1")
    assert a == b and len(a) == 64


@pytest.mark.unit
@pytest.mark.tc("CORE-HASH-010")
@pytest.mark.parametrize("change", [{"numerics_epoch": 4}, {"weights_sha256": "cd" * 32}, {"adapter_version": "1.3.0"}])
def test_core_hash_010_epoch_weights_adapter_change_fingerprint(change):
    assert model_fingerprint(**{**FP, **change}) != model_fingerprint(**FP)
