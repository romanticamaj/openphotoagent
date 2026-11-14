"""The built-in Administrator is printed as an alias in SDDL (GitHub's Windows runner runs as one)."""

import pytest


@pytest.mark.unit
@pytest.mark.os_windows
@pytest.mark.tc("DESK-056")
def test_desk_056_rid_500_account_is_recognised_in_the_dacl():
    """回歸: `D:P(A;;FA;;;LA)...` failed the read-back check, so the app could not start under that account."""
    from opagent_secure._win32 import names_user

    dacl = "D:P(A;;FA;;;LA)(A;;FA;;;SY)"
    assert names_user(dacl, "S-1-5-21-1-2-3-500")
    assert not names_user(dacl, "S-1-5-21-1-2-3-1001")
    assert names_user("D:P(A;;FA;;;S-1-5-21-1-2-3-1001)", "S-1-5-21-1-2-3-1001")
