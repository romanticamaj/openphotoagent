import json
from datetime import datetime
from pathlib import Path

import pytest
from opagent_core.path_template import RenderValues, TemplateError, compile_template, render, sanitize_segment

GOLDEN = json.loads((Path(__file__).resolve().parents[4] / "fixtures/golden/path_template/cases.json").read_text())


@pytest.mark.contract
@pytest.mark.tc("CORE-TPL-025")
def test_core_tpl_025_python_matches_shared_golden():
    cases = GOLDEN["cases"]
    assert len(cases) >= 200
    for c in cases:
        if c["kind"] == "compile_error":
            with pytest.raises(TemplateError) as e:
                compile_template(c["template"])
            assert (e.value.code, e.value.pos) == (c["code"], c["pos"]), c["template"]
        elif c["kind"] == "segment":
            assert sanitize_segment(c["value"]) == c["out"], repr(c["value"])
        else:
            v = c["values"]
            rv = RenderValues(
                stem=v["stem"],
                ext=v["ext"],
                taken_at=datetime.fromisoformat(c["taken_at"]) if c["taken_at"] else None,
                mtime=datetime.fromisoformat(c["mtime"]),
                category=v["category"],
                category_key=v["category_key"],
                country=v["country"],
                admin1=v["admin1"],
                city=v["city"],
                source=v["source"],
                tags=tuple((k, s) for k, s in v["tags"]),
                tag_display=v["tag_display"],
            )
            r = render(compile_template(c["template"]), rv, fallback_values=c["fallback"], lang=c["lang"])
            assert (r.rel, r.date_source) == (c["rel"], c["date_source"])
