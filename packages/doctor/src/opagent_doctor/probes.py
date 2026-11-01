from typing import Any

from opagent_core.hw import HwSummary


def load_profile(name: str) -> Any:
    return HwSummary(os="", arch="")
