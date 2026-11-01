from datetime import datetime, timedelta, timezone

import pytest
from opagent_core.path_template import RenderValues, compile_template, render

TW = timezone(timedelta(hours=8))
BY_DATE = "{taken_at:%Y}/{taken_at:%Y-%m}/{stem}{ext}"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-001")
def test_core_tpl_001_basic_render():
    tpl = compile_template("{category}/{taken_at:%Y}/{taken_at:%Y-%m}/{stem}{ext}")
    r = render(
        tpl,
        RenderValues(stem="IMG_0001", ext=".HEIC", category="美食", taken_at=datetime(2025, 7, 14, 10, 0, tzinfo=TW)),
    )
    assert r.rel == "美食/2025/2025-07/IMG_0001.HEIC"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-004")
def test_core_tpl_004_missing_date_uses_mtime_and_reports_source():
    tpl = compile_template(BY_DATE)
    r = render(
        tpl, RenderValues(stem="IMG_0002", ext=".jpg", taken_at=None, mtime=datetime(2024, 3, 2, 9, 0, tzinfo=TW))
    )
    assert r.rel == "2024/2024-03/IMG_0002.jpg"
    assert r.date_source == "mtime"


@pytest.mark.unit
@pytest.mark.tc("CORE-TPL-004")
def test_core_tpl_004_exif_date_is_not_reported_as_mtime():
    r = render(
        compile_template(BY_DATE),
        RenderValues(
            stem="a", ext=".jpg", taken_at=datetime(2025, 1, 2, tzinfo=TW), mtime=datetime(2024, 3, 2, tzinfo=TW)
        ),
    )
    assert r.rel == "2025/2025-01/a.jpg"
    assert r.date_source != "mtime"
