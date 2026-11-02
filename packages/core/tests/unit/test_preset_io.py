"""Preset export / import (04 §2.2; VERSIONING §4; 09b §6 PRESET): files from older versions migrate, unknown
fields survive a round trip, and a malicious file is refused before anything is stored."""

import pytest
import yaml
from opagent_core import preset_io as P

V1 = """
schema_version: 1
key: my-trips
name: { zh-TW: 我的旅行, en: My trips }
task_configs: []
output_rules:
  - priority: 1
    when: "true"
    action: copy
    path_template: "{taken_at:%Y}/{stem}{ext}"
    conflict_policy: rename
    future_rule_option: keep-me
categories:
  - { key: travel, zh-TW: 旅行, en: Travel, description: 旅途中 }
future_top_level: { a: 1 }
"""


@pytest.mark.unit
@pytest.mark.tc("PRESET-001")
def test_preset_001_v1_file_migrates_and_unknown_fields_stay():
    doc = P.import_text(V1)
    assert doc["schema_version"] == P.SCHEMA_VERSION == 2 and doc["kind"] == "opagent-preset"
    assert doc["migrated_from"] == 1
    pre = doc["preset"]
    assert pre["key"] == "my-trips" and pre["output_rules"][0]["path_template"] == "{taken_at:%Y}/{stem}{ext}"
    assert pre["future_top_level"] == {"a": 1} and pre["output_rules"][0]["future_rule_option"] == "keep-me"
    text = P.export_text(doc, app_version="0.3.0")
    again = yaml.safe_load(text)
    assert again["schema_version"] == 2 and again["app_version"] == "0.3.0"
    assert again["preset"]["future_top_level"] == {"a": 1}
    assert P.import_text(text)["preset"] == pre  # round trip, nothing lost
    assert "migrated_from" not in P.import_text(text)


@pytest.mark.unit
@pytest.mark.tc("PRESET-001")
def test_preset_001_newer_file_is_refused_with_a_clear_code():
    newer = P.export_text(P.import_text(V1), app_version="0.3.0").replace("schema_version: 2", "schema_version: 9")
    with pytest.raises(P.PresetError) as e:
        P.import_text(newer)
    assert e.value.code == "PRESET_TOO_NEW" and e.value.params == {"found": 9, "supported": 2}


def _bad(**over):
    d = yaml.safe_load(V1)
    rule = d["output_rules"][0]
    for k, v in over.items():
        if k in rule:
            rule[k] = v
        else:
            d[k] = v
    return yaml.safe_dump(d, allow_unicode=True)


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("PRESET-002")
@pytest.mark.parametrize(
    ("text", "code"),
    [
        (_bad(path_template="/etc/{stem}"), "TEMPLATE_PATH_ESCAPE"),  # CORE-TPL-018
        (_bad(path_template="../{stem}"), "TEMPLATE_PATH_ESCAPE"),
        (_bad(path_template="{category}/../{stem}"), "TEMPLATE_PATH_ESCAPE"),
        (_bad(path_template="\\\\server\\{stem}"), "TEMPLATE_PATH_ESCAPE"),
        (_bad(when="__import__('os').system('x')"), "RULE_FORBIDDEN"),  # CORE-RULE-006
        (_bad(when="category.__class__"), "RULE_FORBIDDEN"),
        (_bad(action="delete"), "PRESET_INVALID"),
        (_bad(key="../../etc"), "PRESET_INVALID"),
        ("a: &x [1]\nb: [*x, *x]\n", "PRESET_INVALID"),  # aliases: no billion laughs
        ("!!python/object/apply:os.system ['x']\n", "PRESET_INVALID"),  # tags: never constructed
        ("x" * (P.MAX_BYTES + 1), "PRESET_TOO_LARGE"),
        ("[1, 2]", "PRESET_INVALID"),
        ("key: [unclosed\n", "PRESET_INVALID"),  # YAML syntax
        ("schema_version: two\nkey: x\n", "PRESET_INVALID"),
        ("schema_version: 2\nkind: opagent-preset\npreset: [1]\n", "PRESET_INVALID"),
        ("key: ok\noutput_rules: {a: 1}\n", "PRESET_INVALID"),
        ("key: ok\noutput_rules: [1]\n", "PRESET_INVALID"),
        (_bad(conflict_policy="clobber"), "PRESET_INVALID"),
        ("key: ok\ncategories: [{key: '../x'}]\n", "PRESET_INVALID"),
        ("key: ok\nx: !custom 1\n", "PRESET_INVALID"),  # unknown tag
        ("key: ok\nx: &a 1\n", "PRESET_INVALID"),  # an anchor alone is refused too
    ],
)
def test_preset_002_malicious_files_are_refused(text, code):
    with pytest.raises(P.PresetError) as e:
        P.import_text(text)
    assert e.value.code == code


@pytest.mark.unit
@pytest.mark.tc("PRESET-002")
def test_preset_002_dsl_error_points_at_the_rule():
    with pytest.raises(P.PresetError) as e:
        P.import_text(_bad(when="category.__class__"))
    assert e.value.params["pointer"] == "/preset/output_rules/0/when"
