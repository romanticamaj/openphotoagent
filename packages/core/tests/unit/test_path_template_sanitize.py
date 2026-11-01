from datetime import datetime, timedelta, timezone

import pytest
from opagent_core.path_template import RenderValues, compile_template, render, sanitize_segment

TW = timezone(timedelta(hours=8))
JP = timezone(timedelta(hours=9))
D = datetime(2025, 7, 14, 10, 0, tzinfo=TW)


def r(tpl: str, **kw) -> str:
    base = {"stem": "IMG_0001", "ext": ".jpg", "taken_at": D}
    base.update(kw)
    return render(compile_template(tpl), RenderValues(**base)).rel


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-002")
@pytest.mark.parametrize(("alias", "strf"), [("YYYY", "%Y"), ("YYYY-MM", "%Y-%m"), ("MM", "%m"), ("DD", "%d")])
def test_core_tpl_002_date_alias_equivalent(alias, strf):
    assert r(f"{{category}}/{{date:{alias}}}/{{stem}}{{ext}}", category="美食") == r(
        f"{{category}}/{{taken_at:{strf}}}/{{stem}}{{ext}}", category="美食"
    )


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-003")
def test_core_tpl_003_capture_local_time_not_utc():
    assert r("{taken_at:%Y}/{stem}{ext}", taken_at=datetime(2025, 12, 31, 23, 30, tzinfo=JP)) == "2025/IMG_0001.jpg"
    assert r("{taken_at:%Y}/{stem}{ext}", taken_at=datetime(2026, 1, 1, 0, 30, tzinfo=JP)) == "2026/IMG_0001.jpg"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-005")
def test_core_tpl_005_missing_value_fallback():
    tpl = compile_template("{country}/{stem}{ext}")
    v = RenderValues(stem="IMG_0001", ext=".jpg", taken_at=D)
    assert render(tpl, v, fallback_values={"country": "未知地點"}).rel == "未知地點/IMG_0001.jpg"
    assert render(tpl, v, lang="zh-TW").rel == "_未知/IMG_0001.jpg"
    assert render(tpl, v, lang="en").rel == "_unknown/IMG_0001.jpg"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-006")
def test_core_tpl_006_illegal_chars_replaced():
    out = r("{category}/{stem}{ext}", category='a/b\\c:d*e?f"g<h>i|j')
    assert out == "a_b_c_d_e_f_g_h_i_j/IMG_0001.jpg"
    assert out.count("/") == 1


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-007")
def test_core_tpl_007_control_and_bidi_removed():
    val = "a" + "".join(chr(c) for c in range(0x20)) + "\x7fb‮c​d﻿e"
    assert sanitize_segment(val) == "abcde"


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("CORE-TPL-008")
@pytest.mark.parametrize("val", ["..", ".", "..."])
def test_core_tpl_008_dot_values(val):
    out = r("{category}/{stem}{ext}", category=val)
    assert out == "_/IMG_0001.jpg"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-009")
def test_core_tpl_009_trim_spaces_and_trailing_dots():
    assert sanitize_segment("  旅行. ") == "旅行"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-010")
@pytest.mark.parametrize(
    ("val", "want"),
    [
        ("CON", "CON_"),
        ("con", "con_"),
        ("Nul.txt", "Nul_.txt"),
        ("COM1", "COM1_"),
        ("LPT9", "LPT9_"),
        ("AUX ", "AUX_"),
        ("CONSOLE", "CONSOLE"),
    ],
)
def test_core_tpl_010_windows_reserved_names(val, want):
    assert sanitize_segment(val) == want


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-011")
def test_core_tpl_011_slash_lookalikes():
    assert sanitize_segment("a／b∕c⁄d＼e") == "a_b_c_d_e"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-012")
def test_core_tpl_012_segment_byte_limit_on_char_boundary():
    out = sanitize_segment("貓" * 300)
    assert len(out.encode()) <= 255
    assert out == "貓" * 85
