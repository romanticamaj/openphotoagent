import unicodedata
from datetime import datetime, timedelta, timezone

import pytest

from opagent_core.path_template import (
    PathTooLong,
    RenderValues,
    TemplateError,
    check_full_path,
    compile_template,
    render,
    sanitize_segment,
    split_name,
)

D = datetime(2025, 7, 14, 10, 0, tzinfo=timezone(timedelta(hours=8)))


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-013")
def test_core_tpl_013_truncate_keeps_extension():
    out = render(compile_template("{stem}{ext}"), RenderValues(stem="a" * 260, ext=".jpeg", taken_at=D)).rel
    assert out.endswith(".jpeg")
    assert len(out.encode()) <= 255
    assert out == "a" * 250 + ".jpeg"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-014")
def test_core_tpl_014_emoji_sequences_not_split():
    fam = "👨\u200d👩\u200d👧"
    val = "🐱\u200d👤家族" + fam
    for limit in range(1, len(val.encode())):
        out = sanitize_segment(val * 20, limit=limit)
        if out == "_":
            continue
        # never ends inside a ZWJ sequence, never a lone surrogate/ZWJ at the end
        assert not out.endswith("\u200d")
        assert out.encode("utf-8").decode("utf-8") == out
        assert len(out.encode()) <= limit
        if out.endswith(("👨", "👩")):
            pytest.fail(f"split family emoji at limit {limit}: {out[-6:]!r}")


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-015")
def test_core_tpl_015_nfd_to_nfc():
    out = sanitize_segment(unicodedata.normalize("NFD", "Café"))
    assert out == "Café" and unicodedata.is_normalized("NFC", out)


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-016")
def test_core_tpl_016_full_path_too_long_on_windows():
    target = "C:\\" + "t" * 197
    rel = "x" * 80
    res = check_full_path(target, rel, platform="windows", long_paths=False)
    assert isinstance(res, PathTooLong)
    assert res.conflict == "path_too_long" and res.fix == "fs.long_paths"
    assert check_full_path(target, rel, platform="windows", long_paths=True) is None
    assert check_full_path("/" + "t" * 199, rel, platform="linux", long_paths=False) is None


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-017")
def test_core_tpl_017_unknown_variable_at_compile():
    with pytest.raises(TemplateError) as e:
        compile_template("{foo}/{stem}{ext}")
    assert e.value.code == "UNKNOWN_VAR" and e.value.pos == 1


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("CORE-TPL-018")
@pytest.mark.parametrize("tpl", ["/etc/{stem}", "C:\\{stem}", "../{stem}", "{category}/../{stem}", "\\\\server\\{stem}",
                                 "a/./{stem}", "a//{stem}"])
def test_core_tpl_018_template_path_escape(tpl):
    with pytest.raises(TemplateError) as e:
        compile_template(tpl)
    assert e.value.code == "TEMPLATE_PATH_ESCAPE"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-019")
def test_core_tpl_019_strftime_whitelist():
    compile_template("{taken_at:%Y}/{taken_at:%Y-%m-%d}/{stem}{ext}")
    for bad in ("%c", "%x", "%s", "%Z", "%Y%c"):
        with pytest.raises(TemplateError) as e:
            compile_template(f"{{taken_at:{bad}}}/{{stem}}{{ext}}")
        assert e.value.code == "STRFTIME_NOT_ALLOWED"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-020")
def test_core_tpl_020_template_length_limit():
    ok = "{stem}{ext}" + "a" * 189
    assert len(ok) == 200
    compile_template("a" * 189 + "{stem}{ext}")
    with pytest.raises(TemplateError) as e:
        compile_template("a" * 190 + "{stem}{ext}")
    assert e.value.code == "TEMPLATE_TOO_LONG"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-021")
@pytest.mark.parametrize(("name", "stem", "ext"), [("a.b.c.JPG", "a.b.c", ".JPG"), ("README", "README", ""),
                                                   (".hidden", ".hidden", ""), ("x.tar.gz", "x.tar", ".gz")])
def test_core_tpl_021_stem_ext_split(name, stem, ext):
    assert split_name(name) == (stem, ext)


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-022")
def test_core_tpl_022_tag_prefix():
    v = RenderValues(stem="a", ext=".jpg", taken_at=D, tags=(("animal.cat", 0.9), ("animal.dog", 0.4), ("food.x", 1.0)),
                     tag_display={"animal.cat": "貓", "animal.dog": "狗"})
    assert render(compile_template("{tag:animal}/{stem}{ext}"), v).rel == "貓/a.jpg"
    v2 = RenderValues(stem="a", ext=".jpg", taken_at=D, tags=(("food.x", 1.0),))
    assert render(compile_template("{tag:animal}/{stem}{ext}"), v2, lang="en").rel == "_unknown/a.jpg"
