"""FsOps: every filesystem side effect of the executor goes through this protocol (docs/09 §3.3)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol


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


class RealFs:
    def read_hash(self, p: Path) -> str:
        raise NotImplementedError

    def exists(self, p: Path) -> bool:
        return os.path.lexists(p)

    def makedirs(self, p: Path) -> list[Path]:
        return []

    def write_tmp_copy(self, src: Path, dst_dir: Path) -> Path:
        raise NotImplementedError

    def fsync_file(self, p: Path) -> None:
        pass

    def fsync_dir(self, p: Path) -> None:
        pass

    def rename_noreplace(self, src: Path, dst: Path) -> None:
        pass

    def unlink(self, p: Path) -> None:
        pass

    def rmdir(self, p: Path) -> None:
        pass

    def is_empty_dir(self, p: Path) -> bool:
        return False


class RecordingFs:
    """Wraps an FsOps and records the call sequence (OUT-EXE-001, OUT-JRN-001)."""

    SIDE_EFFECTS = {"makedirs", "write_tmp_copy", "fsync_file", "fsync_dir", "rename_noreplace", "unlink", "rmdir"}

    def __init__(self, inner: FsOps, log: list[str] | None = None) -> None:
        self.inner = inner
        self.log: list[str] = log if log is not None else []

    def __getattr__(self, name: str) -> Any:
        fn = getattr(self.inner, name)

        def wrapper(*a: Any, **kw: Any) -> Any:
            self.log.append(name)
            return fn(*a, **kw)

        return wrapper
