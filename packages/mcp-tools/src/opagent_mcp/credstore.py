"""Where the local MCP token lives (05 §3.2.1, MCP-PAT-001): the OS keyring (Keychain, Credential Manager,
Secret Service). Without a usable keyring (headless Linux) it falls back to a 0600 file in the data folder and
says so, so the UI can warn. Client config files never contain the token."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

SERVICE = "openphotoagent"


def _user(profile: str) -> str:
    return f"mcp-pat:{profile}"


def _file(home: Path, profile: str) -> Path:
    return Path(home) / "secrets" / f"mcp-pat-{profile}"


def _keyring() -> Any:
    try:
        import keyring
        from keyring.backends.fail import Keyring as Fail
    except ImportError:
        return None
    kr = keyring.get_keyring()
    if isinstance(kr, Fail) or kr.priority <= 0:  # no Secret Service / Keychain: unusable
        return None
    return keyring


def save(token: str, home: Path, profile: str = "default") -> dict[str, Any]:
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE, _user(profile), token)
            f = _file(home, profile)
            if f.exists():
                f.unlink()
            return {"store": "keyring", "warning": None}
        except Exception:
            pass
    f = _file(home, profile)
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token)
    os.chmod(f, 0o600)
    return {"store": "file", "warning": "KEYRING_UNAVAILABLE", "path": str(f)}


def load(home: Path, profile: str = "default") -> str | None:
    env = os.environ.get("OPAGENT_TOKEN")
    if env:
        return env
    kr = _keyring()
    if kr is not None:
        try:
            v = kr.get_password(SERVICE, _user(profile))
            if v:
                return str(v)
        except Exception:
            pass
    f = _file(home, profile)
    return f.read_text().strip() if f.exists() else None


def delete(home: Path, profile: str = "default") -> None:
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(SERVICE, _user(profile))
        except Exception:
            pass
    f = _file(home, profile)
    if f.exists():
        f.unlink()
