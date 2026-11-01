"""Crash-injection hook for chaos tests (docs/09 §3.3). Active only when OPAGENT_TEST_MODE=1.

``OPAGENT_CRASH_AT=<point>`` (optionally ``OPAGENT_CRASH_NTH=<n>``) makes the process exit with 137 the n-th
time execution reaches that point, simulating kill -9.
"""

from __future__ import annotations

import os

_counts: dict[str, int] = {}


def crash_point(name: str) -> None:
    if os.environ.get("OPAGENT_TEST_MODE") != "1" or os.environ.get("OPAGENT_CRASH_AT") != name:
        return
    _counts[name] = _counts.get(name, 0) + 1
    if _counts[name] >= int(os.environ.get("OPAGENT_CRASH_NTH", "1")):
        os._exit(137)
