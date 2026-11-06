"""Where the local MCP token lives (05 §3.2.1, MCP-PAT-001): the OS keyring (Keychain, Credential Manager,
Secret Service). Without a usable keyring (headless Linux) it falls back to an owner-only file (0600, ADR-0024) in the
data folder and says so, so the UI can warn. Client config files never contain the token."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from opagent_secure import private_dir, write_private

SERVICE = "openphotoagent"


def _user(profile: str, kind: str = "mcp-pat") -> str:
    return f"{kind}:{profile}"


def _file(home: Path, profile: str, kind: str = "mcp-pat") -> Path:
    return Path(home) / "secrets" / f"{kind}-{profile}"


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


def save(token: str, home: Path, profile: str = "default", kind: str = "mcp-pat") -> dict[str, Any]:
    """``kind`` separates secrets: ``mcp-pat`` (the local MCP token), ``assistant-key`` (an external LLM key)."""
    kr = _keyring()
    if kr is not None:
        with contextlib.suppress(Exception):  # any backend error (locked, no D-Bus): use the file instead
            kr.set_password(SERVICE, _user(profile, kind), token)
            f = _file(home, profile, kind)
            if f.exists():
                f.unlink()
            return {"store": "keyring", "warning": None}
    f = _file(home, profile, kind)
    private_dir(f.parent)
    write_private(f, token.encode())
    return {"store": "file", "warning": "KEYRING_UNAVAILABLE", "path": str(f)}


def load(home: Path, profile: str = "default", kind: str = "mcp-pat") -> str | None:
    env = os.environ.get("OPAGENT_TOKEN") if kind == "mcp-pat" else None  # the env override is for the MCP token only
    if env:
        return env
    kr = _keyring()
    if kr is not None:
        with contextlib.suppress(Exception):
            v = kr.get_password(SERVICE, _user(profile, kind))
            if v:
                return str(v)
    f = _file(home, profile, kind)
    return f.read_text(encoding="utf-8").strip() if f.exists() else None


def delete(home: Path, profile: str = "default", kind: str = "mcp-pat") -> None:
    kr = _keyring()
    if kr is not None:
        with contextlib.suppress(Exception):
            kr.delete_password(SERVICE, _user(profile, kind))
    f = _file(home, profile, kind)
    if f.exists():
        f.unlink()
