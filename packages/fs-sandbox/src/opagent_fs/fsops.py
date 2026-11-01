"""FsOps: every filesystem side effect of the executor goes through this protocol (docs/09 §3.3)."""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import errno
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Protocol

import blake3

TMP_PREFIX = ".opagent-tmp-"
CHUNK = 1 << 20


class FsOps(Protocol):
    def read_hash(self, p: Path) -> str: ...
    def exists(self, p: Path) -> bool: ...
    def makedirs(self, p: Path) -> list[Path]: ...
    def write_tmp_copy(self, src: Path, dst_dir: Path) -> Path: ...
    def fsync_file(self, p: Path) -> None: ...
    def fsync_dir(self, p: Path) -> None: ...
    def rename_noreplace(self, src: Path, dst: Path) -> None: ...
    def unlink(self, p: Path) -> None: ...
    def rmdir(self, p: Path) -> None: ...
    def is_empty_dir(self, p: Path) -> bool: ...


def _libc() -> Any:
    name = ctypes.util.find_library("c")
    return ctypes.CDLL(name, use_errno=True) if name else None


_LIBC = _libc() if sys.platform != "win32" else None


class RealFs:
    def read_hash(self, p: Path) -> str:
        h = blake3.blake3()
        with p.open("rb") as fh:
            while chunk := fh.read(CHUNK):
                h.update(chunk)
        return h.hexdigest()

    def exists(self, p: Path) -> bool:
        return os.path.lexists(p)

    def makedirs(self, p: Path) -> list[Path]:
        """Create missing directories; return the ones created (outermost first) for undo."""
        missing: list[Path] = []
        cur = p
        while not os.path.lexists(cur):
            missing.append(cur)
            cur = cur.parent
        for d in reversed(missing):
            d.mkdir()
        return list(reversed(missing))

    def write_tmp_copy(self, src: Path, dst_dir: Path) -> Path:
        tmp = dst_dir / f"{TMP_PREFIX}{uuid.uuid4().hex}"
        try:
            with src.open("rb") as fi, open(tmp, "xb") as fo:
                shutil.copyfileobj(fi, fo, CHUNK)
            shutil.copystat(src, tmp)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp)  # never leave a partial tmp behind (e.g. ENOSPC mid-copy)
            raise
        return tmp

    def fsync_file(self, p: Path) -> None:
        fd = os.open(p, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def fsync_dir(self, p: Path) -> None:
        if sys.platform == "win32":  # directories cannot be fsynced on Windows
            return
        fd = os.open(p, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def rename_noreplace(self, src: Path, dst: Path) -> None:
        """Atomic rename that never overwrites an existing dst (raises FileExistsError)."""
        if sys.platform == "win32":
            os.rename(src, dst)  # fails when dst exists
            return
        if sys.platform.startswith("linux") and _LIBC is not None and hasattr(_LIBC, "renameat2"):
            at_fdcwd, rename_noreplace = -100, 1
            r = _LIBC.renameat2(at_fdcwd, os.fsencode(src), at_fdcwd, os.fsencode(dst), rename_noreplace)
            if r == 0:
                return
            e = ctypes.get_errno()
            if e == errno.EEXIST:
                raise FileExistsError(errno.EEXIST, "exists", str(dst))
            if e not in (errno.EINVAL, errno.ENOSYS):
                raise OSError(e, os.strerror(e), str(dst))
        if sys.platform == "darwin" and _LIBC is not None and hasattr(_LIBC, "renamex_np"):
            rename_excl = 0x4
            if _LIBC.renamex_np(os.fsencode(src), os.fsencode(dst), rename_excl) == 0:
                return
            e = ctypes.get_errno()
            if e == errno.EEXIST:
                raise FileExistsError(errno.EEXIST, "exists", str(dst))
            if e not in (errno.EINVAL, errno.ENOTSUP):
                raise OSError(e, os.strerror(e), str(dst))
        os.link(src, dst)  # fallback: link fails with EEXIST, never overwrites
        os.unlink(src)

    def unlink(self, p: Path) -> None:
        os.unlink(p)

    def rmdir(self, p: Path) -> None:
        os.rmdir(p)

    def is_empty_dir(self, p: Path) -> bool:
        return p.is_dir() and not any(p.iterdir())


class RecordingFs:
    """Wraps an FsOps and records the call sequence (OUT-EXE-001, OUT-JRN-001)."""

    SIDE_EFFECTS = frozenset(
        {"makedirs", "write_tmp_copy", "fsync_file", "fsync_dir", "rename_noreplace", "unlink", "rmdir"}
    )

    def __init__(self, inner: FsOps, log: list[str] | None = None) -> None:
        self.inner = inner
        self.log: list[str] = log if log is not None else []

    def __getattr__(self, name: str) -> Any:
        fn = getattr(self.inner, name)

        def wrapper(*a: Any, **kw: Any) -> Any:
            self.log.append(name)
            return fn(*a, **kw)

        return wrapper
