"""Every breaking change carries a migration note (09b §9.4 MIG-003; VERSIONING §4)."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("check_changelog", ROOT / "tools/check_changelog.py")
cc = importlib.util.module_from_spec(_s)
_s.loader.exec_module(cc)

GOOD = """# Changelog

## [2.0.0] - 2027-05-01

### ⚠ BREAKING CHANGES

* **api:** `GET /api/v1/foo` renamed to `/api/v2/foo`. Migration: call `/api/v2/foo`; v1 is served until 2.1.
* **worker:** protocol 3.0. Migration: update every worker before the control plane.

### Features

* something new
"""

BAD = GOOD.replace("Migration: update every worker before the control plane.", "Workers need updating.")


@pytest.mark.tc("MIG-003")
def test_mig_003_changelog_breaking_entries_need_migration_notes():
    assert cc.check_changelog(GOOD) == []
    bad = cc.check_changelog(BAD)
    assert len(bad) == 1 and "protocol 3.0" in bad[0]
    assert cc.check_changelog((ROOT / "CHANGELOG.md").read_text(encoding="utf-8")) == []  # the real one


@pytest.mark.tc("MIG-003")
def test_mig_003_breaking_commits_need_migration_notes():
    ok = "feat(api)!: drop the v0 search shape\n\nBREAKING CHANGE: `items[].id` is now `asset_id`.\nMigration: read `asset_id`.\n"
    missing = "feat(api)!: drop the v0 search shape\n\nBREAKING CHANGE: `items[].id` is now `asset_id`.\n"
    footer_only = "fix(db): x\n\nBREAKING CHANGE: column renamed\n"
    plain = "feat(api): add a field\n\nTests: X-001\n"
    assert cc.check_commits([ok, plain]) == []
    assert len(cc.check_commits([missing, footer_only, plain])) == 2
