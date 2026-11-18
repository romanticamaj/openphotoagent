"""Owner-only files and folders on every OS (ADR-0024; 08 §1.3).

"0600" and "0700" in the specs mean owner-only. POSIX gets exactly those modes. On Windows ``os.chmod`` cannot express
that, so the same property is a protected DACL holding the current user and SYSTEM, set when the object is created so
a secret is never readable under the inherited ACL, not even between create and write.

A leaf package with no dependencies: cli, worker and mcp-tools all need it, and apps/api reaches it through mcp-tools,
which rules out opagent_fs (invariant 1).

Every function returns True when the protection is in place, False on a volume that cannot hold it (FAT, exFAT, some
network shares: there the file is written anyway and a warning is logged). An ACL-capable volume that does not end up
with the protection raises PermissionError instead: it fails closed.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

__all__ = ["private_dir", "restrict", "write_private"]

log = logging.getLogger(__name__)


def _tmp_for(path: Path) -> Path:
    return path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")


def _warn_unprotected(path: Path) -> bool:
    log.warning("SECRET_UNPROTECTED_VOLUME: this volume keeps no ACLs, %s is readable by other users", path)
    return False


def _open_private(tmp: Path) -> tuple[int, bool]:
    """A new file nobody else can read, and whether that protection holds."""
    if sys.platform == "win32":
        from . import _win32

        if not _win32.has_acls(tmp):
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY | os.O_NOINHERIT, 0o600)
            return fd, _warn_unprotected(tmp)
        return _win32.create_private(tmp), True
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)  # a restrictive mode must not depend on the umask being sane in the other direction
    return fd, True


def write_private(path: Path | str, data: bytes, *, on_open: Callable[[Path], None] | None = None) -> bool:
    """Atomically replace ``path`` with ``data``, owner-only from the first byte.

    ``on_open`` runs with the temporary file's path before any byte is written (tests inspect the ACL there)."""
    path = Path(path)
    tmp = _tmp_for(path)
    fd, protected = _open_private(tmp)
    try:
        with os.fdopen(fd, "wb") as f:
            if on_open is not None:
                on_open(tmp)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # a same-volume rename keeps the temporary file's protection
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return protected


def restrict(path: Path | str) -> bool:
    """Make an existing file (0600) or folder (0700) owner-only. Files inside a folder that inherit from it follow."""
    path = Path(path)
    if sys.platform == "win32":
        from . import _win32

        if not _win32.has_acls(path):
            return _warn_unprotected(path)
        _win32.restrict(path)
        return True
    os.chmod(path, 0o700 if path.is_dir() else 0o600)
    return True


def private_dir(path: Path | str) -> bool:
    """Create the folder if needed (with its parents) and make it owner-only."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return restrict(path)
