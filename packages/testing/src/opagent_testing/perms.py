"""Owner-only assertions that work on every OS (ADR-0024).

The Windows branch is the test oracle for ``opagent_secure``, so it shares no code with it: the ACL comes from
``icacls /save`` (locale-free SDDL) and the SID from ``whoami``, both outside this process."""

from __future__ import annotations

import csv
import os
import re
import stat
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

AUTHENTICATED_USERS = "*S-1-5-11"


@lru_cache(maxsize=1)
def _me() -> str:
    out = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, encoding="oem", check=True
    ).stdout
    return next(csv.reader([out.strip()]))[1]


def sddl(path: Path | str) -> str:
    """The DACL of ``path`` as SDDL, on Windows."""
    fd, out = tempfile.mkstemp(suffix=".acl")
    os.close(fd)
    try:
        subprocess.run(["icacls", str(path), "/save", out], capture_output=True, check=True)
        lines = Path(out).read_bytes().decode("utf-16").splitlines()
    finally:
        os.unlink(out)
    return lines[1].strip()


def assert_owner_only(path: Path | str, mode: int) -> None:
    """POSIX: the exact mode. Windows: a protected DACL with exactly the user and SYSTEM, full control."""
    path = Path(path)
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == mode, oct(stat.S_IMODE(path.stat().st_mode))
        return
    d = sddl(path)
    inherit = "OICI" if path.is_dir() else ""
    assert d.startswith("D:P"), f"{path}: DACL is not protected from inheritance: {d}"
    me = "LA" if _me().endswith("-500") else _me()  # SDDL prints the built-in Administrator as an alias
    want = {f"(A;{inherit};FA;;;{me})", f"(A;{inherit};FA;;;SY)"}
    assert set(re.findall(r"\([^)]*\)", d)) == want, f"{path}: {d}"


def widen(path: Path | str, mode: int) -> None:
    """Give ``path`` the permissions a careless older install or a data folder on D:\\ would have."""
    if sys.platform != "win32":
        os.chmod(path, mode)
        return
    subprocess.run(
        ["icacls", str(path), "/inheritance:e", "/grant", f"{AUTHENTICATED_USERS}:(OI)(CI)M"],
        capture_output=True,
        check=True,
    )
