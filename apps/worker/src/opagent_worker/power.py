"""OS power source for ResourcePolicy (06 §5)."""

from __future__ import annotations

from pathlib import Path


class SysfsPower:
    def __init__(self, root: Path = Path("/sys/class/power_supply")) -> None:
        self.root = Path(root)

    @property
    def on_ac(self) -> bool:
        return True
