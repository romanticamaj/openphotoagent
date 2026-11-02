"""Per-OS application directories (08 §1.5). The shell passes them in; this mirrors Tauri's path API so the CLI
and the shell agree. ``OPAGENT_HOME`` puts everything under one folder (portable mode)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath

NAME = "OpenPhotoAgent"
LOWER = "openphotoagent"


def app_dirs(os_name: str, env: Mapping[str, str], home: str) -> dict[str, str]:
    portable = env.get("OPAGENT_HOME")
    if portable:
        base = PureWindowsPath(portable) if os_name == "windows" else PurePosixPath(portable)
        return {k: str(base / k) if k != "data" else str(base) for k in _KEYS}
    if os_name == "windows":
        local = PureWindowsPath(env.get("LOCALAPPDATA") or f"{home}\\AppData\\Local") / NAME
        roaming = PureWindowsPath(env.get("APPDATA") or f"{home}\\AppData\\Roaming") / NAME
        return {
            "data": str(local),
            "config": str(roaming),
            "models": str(local / "models"),
            "packs": str(local / "packs"),
            "cache": str(local / "cache"),
            "logs": str(local / "logs"),
            "backups": str(local / "backups"),
        }
    h = PurePosixPath(home)
    if os_name == "macos":
        sup = h / "Library" / "Application Support" / NAME
        return {
            "data": str(sup),
            "config": str(sup / "config"),
            "models": str(sup / "models"),
            "packs": str(sup / "packs"),
            "cache": str(h / "Library" / "Caches" / NAME),
            "logs": str(h / "Library" / "Logs" / NAME),
            "backups": str(sup / "backups"),
        }
    data = PurePosixPath(env.get("XDG_DATA_HOME") or h / ".local" / "share") / LOWER
    return {
        "data": str(data),
        "config": str(PurePosixPath(env.get("XDG_CONFIG_HOME") or h / ".config") / LOWER),
        "models": str(data / "models"),
        "packs": str(data / "packs"),
        "cache": str(PurePosixPath(env.get("XDG_CACHE_HOME") or h / ".cache") / LOWER),
        "logs": str(PurePosixPath(env.get("XDG_STATE_HOME") or h / ".local" / "state") / LOWER / "logs"),
        "backups": str(data / "backups"),
    }


_KEYS = ("data", "config", "models", "packs", "cache", "logs", "backups")
