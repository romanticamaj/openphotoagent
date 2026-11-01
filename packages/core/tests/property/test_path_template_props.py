import unicodedata
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from opagent_core.path_template import RenderValues, compile_template, render

D = datetime(2025, 7, 14, 10, 0, tzinfo=timezone(timedelta(hours=8)))
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
TPL = compile_template("{category}/{country}/{city}/{taken_at:%Y}/{stem}{ext}")

nasty = st.one_of(
    st.text(),
    st.sampled_from(["..", ".", "...", "", " ", "CON", "nul.txt", "a/b", "\\x", "\x00", "‮", "🐱‍👤", "貓" * 300]),
    st.text(alphabet=st.characters(codec="utf-8"), min_size=200, max_size=400),
)


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-023")
def test_core_tpl_023_casefold_key():
    a = render(TPL, RenderValues(stem="IMG", ext=".jpg", taken_at=D, category="Food", country="TW", city="x"))
    b = render(TPL, RenderValues(stem="IMG", ext=".jpg", taken_at=D, category="food", country="TW", city="x"))
    assert a.rel != b.rel
    assert a.casefold_key == b.casefold_key == "food/tw/x/2025/img.jpg"


@pytest.mark.property
@pytest.mark.tc("CORE-TPL-024")
@settings(max_examples=300, deadline=None)
@given(category=nasty, country=nasty, city=nasty, stem=nasty)
def test_core_tpl_024_every_segment_is_safe(category, country, city, stem):
    r = render(TPL, RenderValues(stem=stem, ext=".jpg", taken_at=D, category=category, country=country, city=city))
    segs = r.rel.split("/")
    assert len(segs) == 5
    for s in segs:
        assert s and "/" not in s and "\\" not in s and "\x00" not in s
        assert s not in (".", "..")
        assert len(s.encode("utf-8")) <= 255
        assert unicodedata.is_normalized("NFC", s)
        assert s.split(".")[0].upper() not in RESERVED
