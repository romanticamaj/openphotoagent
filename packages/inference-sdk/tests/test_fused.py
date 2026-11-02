import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from opagent_inference.fused import (
    CategoryMap,
    OutputParseError,
    fused_schema,
    parse_fused,
)
from opagent_inference.prompts import CaptionConfig, render_fused_prompt
from opagent_inference.tags import load_vocabulary, normalize_tags

CATS = CategoryMap(
    keys=["food", "pets", "travel", "other"],
    display={"food": {"zh-Hant": "美食", "en": "Food"}, "pets": {"zh-Hant": "寵物", "en": "Pets"}},
    aliases={"food": ["meal", "dish"]},
)
GOOD = {"caption": "一碗拉麵", "tags": ["拉麵", "湯"], "category": "food", "confidence": "high"}


@pytest.mark.unit
@pytest.mark.tc("INF-JSON-001")
def test_inf_json_001_valid():
    r = parse_fused(json.dumps(GOOD, ensure_ascii=False), CATS)
    assert (r.caption, r.tags, r.category, r.confidence) == ("一碗拉麵", ["拉麵", "湯"], "food", "high")
    assert r.repaired is False and r.flags == []


@pytest.mark.unit
@pytest.mark.tc("INF-JSON-002")
def test_inf_json_002_fences_and_chatter():
    raw = "以下是結果：\n```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```\n希望有幫助"
    r = parse_fused(raw, CATS)
    assert r.category == "food" and r.repaired is True


@pytest.mark.unit
@pytest.mark.tc("INF-JSON-003")
def test_inf_json_003_truncated_repair_then_retry():
    trunc = json.dumps(GOOD, ensure_ascii=False)[:-12]
    r = parse_fused(trunc, CATS)
    assert r.repaired and r.caption == "一碗拉麵"
    calls = []

    def retry():
        calls.append(1)
        return json.dumps(GOOD)

    r2 = parse_fused("完全不是 JSON", CATS, retry=retry)
    assert calls == [1] and r2.category == "food" and r2.retried
    with pytest.raises(OutputParseError) as e:
        parse_fused("still not json", CATS, retry=lambda: "nope")
    assert e.value.raw == "still not json"


@pytest.mark.unit
@pytest.mark.tc("INF-JSON-004")
@pytest.mark.parametrize(
    "given_cat,expected", [("美食", "food"), ("Food", "food"), ("meal", "food"), ("FOOD", "food"), ("寵物", "pets")]
)
def test_inf_json_004_alias_mapping(given_cat, expected):
    r = parse_fused(json.dumps({**GOOD, "category": given_cat}, ensure_ascii=False), CATS)
    assert r.category == expected and "enum_violation" not in r.flags


@pytest.mark.unit
@pytest.mark.tc("INF-JSON-004")
def test_inf_json_004_unmappable_is_other():
    r = parse_fused(json.dumps({**GOOD, "category": "spaceships"}), CATS)
    assert r.category == "other" and r.flags == ["enum_violation"]


@pytest.mark.unit
@pytest.mark.tc("INF-JSON-005")
def test_inf_json_005_extra_fields_dropped():
    r = parse_fused(json.dumps({**GOOD, "mood": "happy", "x": 1}, ensure_ascii=False), CATS)
    assert r.category == "food" and set(r.extra_dropped) == {"mood", "x"} and r.warnings


@pytest.mark.unit
@pytest.mark.tc("INF-JSON-006")
def test_inf_json_006_length_limits():
    long = {"caption": "字" * 900, "tags": [f"t{i}" for i in range(25)] + ["長" * 60], "category": "food"}
    long["tags"].insert(0, "長" * 60)
    r = parse_fused(json.dumps(long, ensure_ascii=False), CATS)
    assert len(r.caption) == 400 and len(r.tags) == 20
    assert all(len(t) <= 40 for t in r.tags) and r.tags[0] == "長" * 40


@pytest.mark.unit
@pytest.mark.tc("INF-JSON-007")
def test_inf_json_007_schema_key_order():
    s = fused_schema(CATS)
    assert list(s["properties"])[:3] == ["caption", "tags", "category"]
    assert s["properties"]["category"]["enum"] == ["food", "pets", "travel", "other"]
    assert s["additionalProperties"] is False and s["$id"] == "opagent.fused.v1"


@pytest.mark.property
@pytest.mark.tc("INF-JSON-008")
@settings(max_examples=400, deadline=None)
@given(data=st.data())
def test_inf_json_008_parser_never_crashes(data):
    seed = json.dumps(GOOD, ensure_ascii=False).encode()
    b = bytearray(seed)
    for _ in range(data.draw(st.integers(1, 8))):
        op = data.draw(st.sampled_from(["flip", "del", "ins"]))
        i = data.draw(st.integers(0, max(0, len(b) - 1)))
        if op == "flip" and b:
            b[i] ^= data.draw(st.integers(1, 255))
        elif op == "del" and b:
            del b[i]
        else:
            b.insert(i, data.draw(st.integers(0, 255)))
    raw = bytes(b).decode("utf-8", errors="replace")
    try:
        r = parse_fused(raw, CATS)
    except OutputParseError:
        return
    assert r.category in CATS.keys and isinstance(r.caption, str) and all(isinstance(t, str) for t in r.tags)
    assert len(r.tags) <= 20 and len(r.caption) <= 400


@pytest.mark.unit
@pytest.mark.tc("INF-JSON-009")
def test_inf_json_009_caption_lang_and_length():
    p1 = render_fused_prompt(CATS, CaptionConfig(languages=["zh-Hant"], length="short"))
    assert "台灣繁體中文" in p1.user and "一句話" in p1.user
    p2 = render_fused_prompt(CATS, CaptionConfig(languages=["en"], length="long"))
    assert "English" in p2.user and "120" in p2.user and "台灣繁體中文" not in p2.user
    r = parse_fused(json.dumps(GOOD, ensure_ascii=False), CATS, caption_lang="zh-Hant")
    assert r.caption_lang == "zh-Hant"


@pytest.mark.unit
@pytest.mark.tc("INF-JSON-010")
def test_inf_json_010_synonyms_merge():
    vocab = load_vocabulary()
    out = normalize_tags([("狗", 0.9), ("犬", 0.8), ("dog", 0.7)], vocab)
    assert [t.canonical_id for t in out.tags] == ["animal.dog"]
    assert out.tags[0].label == "狗"


@pytest.mark.unit
@pytest.mark.tc("INF-JSON-011")
def test_inf_json_011_new_tag_cap():
    vocab = load_vocabulary()
    news = [(f"新詞{i}", 0.5 + i / 100) for i in range(12)]
    out = normalize_tags(news, vocab, max_new=5)
    kept = [t for t in out.tags if t.is_new]
    assert len(kept) == 5 and {t.label for t in kept} == {f"新詞{i}" for i in range(7, 12)}
    assert out.dropped_new == 7


@pytest.mark.unit
@pytest.mark.tc("INF-JSON-012")
def test_inf_json_012_contains_image_text():
    r = parse_fused(json.dumps({**GOOD, "contains_image_text": True}, ensure_ascii=False), CATS)
    assert r.contains_image_text is True and "contains_image_text" not in r.extra_dropped
    assert "contains_image_text" in fused_schema(CATS)["properties"]
