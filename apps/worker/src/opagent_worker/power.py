"""OS power source for ResourcePolicy (06 §5, DESK-023): heavy work pauses on battery and resumes on AC.

Read live on every check (the policy asks before each heavy batch), so plugging in resumes without a restart.
* Linux: ``/sys/class/power_supply`` — on battery only when a battery is discharging and no mains supply is online.
* macOS: ``pmset -g batt`` ("AC Power" / "Battery Power").
* Windows: ``GetSystemPowerStatus`` (``ACLineStatus``).
Anything unreadable counts as mains power: a wrong "on battery" would stall work for no reason.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class SysfsPower:
    def __init__(self, root: Path = Path("/sys/class/power_supply")) -> None:
        self.root = Path(root)

    @property
    def on_ac(self) -> bool:
        if not self.root.is_dir():
            return True
        mains_online = False
        discharging = False
        for d in self.root.iterdir():
            typ = _read(d / "type")
            if typ == "Mains" and _read(d / "online") == "1":
                mains_online = True
            elif typ == "Battery" and _read(d / "status") == "Discharging":
                discharging = True
        return mains_online or not discharging


class MacPower:
    @property
    def on_ac(self) -> bool:
        try:
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return True
        return "Battery Power" not in out


class WindowsPower:
    @property
    def on_ac(self) -> bool:  # pragma: no cover - exercised on the Windows CI runner
        if sys.platform != "win32":  # also tells mypy that windll exists below, on every checking OS
            return True
        import ctypes

        class SPS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        s = SPS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return True
        return bool(s.ACLineStatus != 0)  # 0 = offline (battery), 1 = online, 255 = unknown


def system_power() -> SysfsPower | MacPower | WindowsPower:
    if sys.platform == "darwin":
        return MacPower()
    if sys.platform == "win32":
        return WindowsPower()
    return SysfsPower()
