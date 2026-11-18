from pathlib import Path

import pytest
from opagent_core.rules import Rule, RuleError, compile_when, evaluate, final_label, load_preset, validate_custom

PRESETS = Path(__file__).resolve().parents[4] / "apps/api/src/opagent_api/presets/builtin"


def item(**kw):
    base = {"category": "food", "decision": "accepted", "confidence": 0.9, "tags": [], "exif": {"is_screenshot": False}}
    base.update(kw)
    return base


@pytest.mark.unit
@pytest.mark.tc("CORE-RULE-001")
def test_core_rule_001_first_hit_by_priority():
    rules = [Rule(2, "true", target_id="B"), Rule(1, "category == 'food'", target_id="A")]
    assert evaluate(rules, item()).target_id == "A"


@pytest.mark.unit
@pytest.mark.tc("CORE-RULE-002")
def test_core_rule_002_per_category_targets():
    rules = [Rule(1, "category == 'documents'", target_id="S"), Rule(2, "category == 'food'", target_id="F")]
    assert evaluate(rules, item(category="documents")).target_id == "S"
    assert evaluate(rules, item(category="food")).target_id == "F"


@pytest.mark.unit
@pytest.mark.tc("CORE-RULE-003")
def test_core_rule_003_unmatched_falls_to_preset_other():
    p = load_preset((PRESETS / "by-content-12.yaml").read_text(encoding="utf-8"))
    user = [Rule(1, "category == 'pets'", target_id="P")]
    rules = user + p.output_rules
    photos = [item(category=c, decision=d) for c in ("food", "pets", "art") for d in ("accepted", "other")]
    hits = [evaluate(rules, ph) for ph in photos]
    assert all(h is not None for h in hits)  # total in plan == total input
    assert hits[0].fallback_category is None and hits[1].fallback_category == "other"


@pytest.mark.unit
@pytest.mark.tc("CORE-RULE-004")
def test_core_rule_004_require_reviewed():
    rules = [Rule(1, "true", target_id="T", require_reviewed=True), Rule(99, "true", fallback_category="other")]
    pending = [item(decision="needs_review") for _ in range(3)]
    assert [evaluate(rules, p) for p in pending] == [None, None, None]
    escaped = [evaluate(rules, p, skip_unreviewed_to_other=True) for p in pending]
    assert all(r is not None and r.fallback_category == "other" for r in escaped)
    assert evaluate(rules, item(decision="accepted")).target_id == "T"


@pytest.mark.unit
@pytest.mark.tc("CORE-RULE-005")
def test_core_rule_005_syntax_error_has_pointer_and_column():
    with pytest.raises(RuleError) as e:
        compile_when("category == ", pointer="/output_rules/0/when")
    assert e.value.code == "RULE_SYNTAX"
    assert e.value.pointer == "/output_rules/0/when" and e.value.column >= 1


@pytest.mark.security
@pytest.mark.tc("CORE-RULE-006")
@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os')",
        "a.__class__",
        "category.__class__",
        "len(tags)",
        "open('x')",
        "(lambda: 1)()",
        "[x for x in tags]",
        "category[0]",
        "unknown_field == 1",
        "exif.__dict__",
    ],
)
def test_core_rule_006_dsl_sandbox(expr):
    with pytest.raises(RuleError) as e:
        compile_when(expr)
    assert e.value.code in ("RULE_FORBIDDEN", "RULE_UNKNOWN_FIELD")


@pytest.mark.unit
@pytest.mark.tc("CORE-RULE-006")
def test_core_rule_006_allowed_constructs():
    f = compile_when("(category in ['food', 'pets'] and confidence >= 0.8) or not exif.is_screenshot == false")
    assert f(item()) is True


@pytest.mark.unit
@pytest.mark.tc("CORE-RULE-007")
def test_core_rule_007_by_date_needs_no_model():
    p = load_preset((PRESETS / "by-date.yaml").read_text(encoding="utf-8"))
    assert p.key == "by-date" and p.task_configs == []
    assert p.output_rules[0].path_template == "{taken_at:%Y}/{taken_at:%Y-%m}/{stem}{ext}"


@pytest.mark.contract
@pytest.mark.tc("CORE-RULE-008")
def test_core_rule_008_by_content_12_complete():
    p = load_preset((PRESETS / "by-content-12.yaml").read_text(encoding="utf-8"))
    names = [c["zh-TW"] for c in p.categories]
    assert names == [
        "人物",
        "風景",
        "美食",
        "動物",
        "文件與截圖",
        "旅行",
        "活動",
        "建築",
        "植物",
        "交通",
        "藝術",
        "其他",
    ]
    for c in p.categories:
        assert c["zh-TW"] and c["en"] and c["description"]["zh-TW"] and c["description"]["en"]
    assert [c["key"] for c in p.categories if c.get("fallback")] == ["other"]


@pytest.mark.unit
@pytest.mark.tc("CORE-RULE-009")
def test_core_rule_009_custom_needs_description_or_example():
    with pytest.raises(RuleError) as e:
        validate_custom([{"name": "貓咪", "description": "", "examples": []}])
    assert e.value.code == "CUSTOM_CATEGORY_UNDERSPECIFIED"
    validate_custom([{"name": "貓咪", "description": "", "examples": ["a", "b", "c"]}])
    validate_custom([{"name": "貓咪", "description": "家裡的貓", "examples": []}])


@pytest.mark.unit
@pytest.mark.tc("CORE-RULE-010")
def test_core_rule_010_final_label_prefers_review():
    r = {"category": "food", "tags": ["x"], "caption": "c"}
    assert final_label(r, {"status": "corrected", "corrected": {"category": "pets"}}) == {
        "category": "pets",
        "tags": ["x"],
        "caption": "c",
        "source": "review",
    }
    assert final_label(r, None)["source"] == "model"
    assert final_label(r, {"status": "pending"})["category"] == "food"
