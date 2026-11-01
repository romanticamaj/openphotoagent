"""Boundary cases for path_template (strengthen CORE-TPL-* against mutation; 09 §5.6)."""

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest
from opagent_core.path_template import (
    PathTooLong,
    RenderResult,
    RenderValues,
    TemplateError,
    check_full_path,
    compile_template,
    render,
    sanitize_segment,
    truncate_bytes,
)

D = datetime(2025, 7, 4, 9, 5, 6, tzinfo=timezone(timedelta(hours=8)))


def r(tpl, **kw):
    return render(compile_template(tpl), RenderValues(stem="s", ext=".jpg", taken_at=D, **kw)).rel


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-002")
def test_all_date_aliases_and_defaults():
    assert r("{date:YYYY-MM-DD}/{stem}{ext}") == "2025-07-04/s.jpg"
    assert r("{date:DD}/{date:MM}/{stem}{ext}") == "04/07/s.jpg"
    assert r("{date}/{stem}{ext}") == "2025/s.jpg"
    assert r("{taken_at}/{stem}{ext}") == "2025-07-04/s.jpg"
    assert r("{taken_at:%H%M%S %y %j %%}/{stem}{ext}") == "090506 25 185 %/s.jpg"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-007")
@pytest.mark.parametrize("ch", ["\x1f", "\u202a", "\u202d", "\u202e", "\u2066", "\u2069", "\u200e", "\u200f"])
def test_removed_invisible_range_edges(ch):
    assert sanitize_segment(f"a{ch}b") == "ab"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-007")
@pytest.mark.parametrize("ch", [" ", "\u202f", "\u206a", "\u2065", "\u2029"[:0] + "!"])
def test_kept_neighbours_of_removed_ranges(ch):
    assert sanitize_segment(f"a{ch}b") == f"a{ch}b"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-010")
@pytest.mark.parametrize(
    ("val", "want"),
    [
        ("COM9", "COM9_"),
        ("LPT1", "LPT1_"),
        ("COM0", "COM0"),
        ("COM10", "COM10"),
        ("LPT10", "LPT10"),
        ("PRN", "PRN_"),
        ("nul", "nul_"),
    ],
)
def test_reserved_name_ranges(val, want):
    assert sanitize_segment(val) == want


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-014")
@pytest.mark.parametrize(
    "cluster",
    [
        "e\u0301",
        "👍\U0001f3fb",
        "👍\U0001f3ff",
        "❤\ufe0f",
        "❤\ufe00",
        "🏴\U000e0067\U000e007f",
        "🏴\U000e0020\U000e007f",
    ],
)
def test_grapheme_extenders_not_split(cluster):
    s = "a" * 3 + cluster
    for limit in range(3, len(s.encode())):
        assert truncate_bytes(s, limit) == "aaa"
    assert truncate_bytes(s, len(s.encode())) == s


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-014")
def test_grapheme_neighbours_are_separate():
    for ch in ("\U0001f3fa", "\U0001f400", "\ufe10", "\U000e0080"):
        s = "aaa" + ch
        assert truncate_bytes(s, 3) == "aaa"
    assert truncate_bytes("ab", 1) == "a"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-016")
def test_full_path_exact_limits():
    assert check_full_path("C:\\" + "t" * 197 + "\\", "x" * 58, "windows", False) is None  # 200 + 1 + 58 = 259
    hit = check_full_path("C:\\" + "t" * 197, "x" * 59, "windows", False)
    assert isinstance(hit, PathTooLong) and (hit.length, hit.limit) == (260, 259)
    assert check_full_path("/" + "t" * 4000, "x" * 93, "linux", False) is None  # 4001 + 1 + 93 = 4095 bytes
    assert check_full_path("/" + "t" * 4000 + "/", "x" * 93, "linux", False) is None
    big = check_full_path("/" + "t" * 4000, "x" * 94, "linux", False)
    assert isinstance(big, PathTooLong) and big.limit == 4095


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-017")
@pytest.mark.parametrize(
    "var", ["category", "category_key", "taken_at", "date", "stem", "ext", "country", "admin1", "city", "source"]
)
def test_every_known_var_compiles(var):
    compile_template(f"{{{var}}}/{{stem}}{{ext}}")


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-017")
def test_template_error_message_and_positions():
    with pytest.raises(TemplateError) as e:
        compile_template("ab/{nope}")
    assert e.value.pos == 4 and "UNKNOWN_VAR" in str(e.value)
    assert TemplateError("X").pos == 0


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-018")
@pytest.mark.parametrize("tpl", ["C:{stem}", "z:{stem}"])
def test_drive_without_backslash(tpl):
    with pytest.raises(TemplateError) as e:
        compile_template(tpl)
    assert e.value.code == "TEMPLATE_PATH_ESCAPE"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-019")
@pytest.mark.parametrize("fmt", ["%Y%", "x%c", "%Yx%c", "%"])
def test_strftime_edge_cases(fmt):
    with pytest.raises(TemplateError):
        compile_template(f"{{taken_at:{fmt}}}/{{stem}}{{ext}}")


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-005")
def test_default_language_is_zh_tw():
    assert render(compile_template("{country}/{stem}{ext}"), RenderValues(stem="s", ext=".jpg", taken_at=D)).rel == (
        "_未知/s.jpg"
    )


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-024")
def test_value_objects_are_frozen():
    v = RenderValues(stem="s", ext=".jpg")
    res = RenderResult("a", None)
    tpl = compile_template("{stem}{ext}")
    for obj in (v, res, tpl, tpl.parts[0], PathTooLong(1, 2)):
        with pytest.raises(dataclasses.FrozenInstanceError):
            obj.__setattr__("x_y", 1)
