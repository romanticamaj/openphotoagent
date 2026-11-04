"""The v1.0 release checklist covers every open blocker and every v1.0 test (09b §9.5 REL-010)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.tc("REL-010")
def test_rel_010_checklist_lists_manual_items_and_blockers():
    text = (ROOT / "docs/checklists/release-v1.0.md").read_text(encoding="utf-8")
    blockers = re.findall(r"^\| (B-\d+) \| ([^|]+)\|", (ROOT / "docs/BLOCKERS.md").read_text(encoding="utf-8"), re.M)
    open_ = [b for b, kind in blockers if "已清除" not in kind]
    assert open_ and [b for b in open_ if b not in text] == []
    v10 = re.findall(
        r"^\| ([A-Z0-9-]+-\d{3}) \|",
        (ROOT / "docs/09b-test-cases-v0.2-plus.md").read_text(encoding="utf-8").split("## 9. v1.0")[1],
        re.M,
    )
    assert len(v10) >= 20 and [t for t in v10 if t not in text] == []
    for heading in ("自動化", "手動", "發版"):
        assert heading in text
