"""WATCH-007 (回歸): the worker asks Windows which drives are network shares; there is no /proc/mounts there."""

import os

import pytest
from opagent_worker.watcher import _read_mounts, _windows_drive_types


@pytest.mark.unit
@pytest.mark.os_windows
@pytest.mark.tc("WATCH-007")
def test_watch_007_real_drive_types_on_windows():
    types = _windows_drive_types()
    assert types[os.environ["SYSTEMDRIVE"][0].upper()] == 3  # DRIVE_FIXED
    assert isinstance(_read_mounts(), str)
