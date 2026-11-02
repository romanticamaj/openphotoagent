"""Low-risk auto-approval settings (05 §3.6 item 4; 09b TRASH-007): destructive actions are never eligible."""

import pytest
from opagent_core.auto_approve import AutoApproveError, validate_auto_approve


@pytest.mark.unit
@pytest.mark.tc("TRASH-007")
@pytest.mark.parametrize("bad", ["trash", "move", "hardlink"])
def test_trash_007_destructive_actions_are_rejected(bad):
    with pytest.raises(AutoApproveError) as e:
        validate_auto_approve({"actions": ["copy", bad], "max_actions": 100})
    assert e.value.code == "AUTO_APPROVE_DESTRUCTIVE" and bad in e.value.actions


@pytest.mark.unit
@pytest.mark.tc("TRASH-007")
def test_trash_007_non_destructive_settings_pass_and_are_normalised():
    cfg = validate_auto_approve({"actions": ["sidecar_xmp", "copy", "copy"], "max_actions": 99999, "targets": ["sorted"]})
    assert cfg == {"actions": ["copy", "sidecar_xmp"], "max_actions": 500, "targets": ["sorted"]}  # capped at 500
    with pytest.raises(AutoApproveError):
        validate_auto_approve({"actions": ["copy"], "max_actions": 10, "overwrite": True})
    with pytest.raises(AutoApproveError):
        validate_auto_approve({"actions": ["rm -rf"]})  # unknown actions are refused, not ignored
