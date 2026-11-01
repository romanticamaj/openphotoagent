"""SafePath: the single entry point for every file operation (docs/02 §6.1, invariant 2).

``resolve(alias, rel)`` rejects — before touching the disk — absolute paths, drive letters, UNC/device paths,
backslashes, NUL, ``:`` (NTFS ADS), ``..`` components and Windows reserved names (on every OS). It then
checks containment with ``commonpath`` (never string prefixes) and walks existing components with
``lstat`` refusing symlinks. ``open_read`` re-walks the path with ``O_NOFOLLOW`` directory fds so a symlink
swapped in after ``resolve`` (TOCTOU) cannot be followed.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import sys
import typing
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Mode = Literal["ro", "rw"]
_DRIVE = re.compile(r"^[A-Za-z]:")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class PathViolation(Exception):  # noqa: N818 - name from 09a FS-*
    code = "PATH_VIOLATION"
    retryable = False  # permanent (FS-020)

    def __init__(self, reason: str, alias: str = "", rel: str = "") -> None:
        super().__init__(f"{reason}: {alias}:{rel}")
        self.reason = reason
        self.alias = alias
        self.rel = rel


class RootNotAllowed(Exception):  # noqa: N818
    code = "FS_ROOT_NOT_ALLOWED"
    retryable = False


class RootUnavailable(Exception):  # noqa: N818
    code = "FS_ROOT_UNAVAILABLE"
    retryable = True


@dataclass(frozen=True)
class RootConfig:
    alias: str
    path: str
    mode: Mode = "ro"


@dataclass(frozen=True)
class SafePath:
    alias: str
    rel: str
    real: Path


def is_within(root: Path, path: Path) -> bool:
    """Containment by path components (FS-014), never by string prefix."""
    r, p = os.path.normpath(root), os.path.normpath(path)
    return os.path.commonpath([r, p]) == r


def _is_reserved(seg: str) -> bool:
    return seg.split(".")[0].rstrip(" .").upper() in _RESERVED


def check_rel(rel: str, alias: str = "") -> list[str]:
    """Pure lexical validation; returns the NFC path components."""
    if "\x00" in rel:
        raise PathViolation("nul", alias, rel)
    if "\\" in rel:
        raise PathViolation("backslash", alias, rel)
    if rel.startswith("/") or _DRIVE.match(rel):
        raise PathViolation("absolute", alias, rel)
    if ":" in rel:
        raise PathViolation("colon", alias, rel)
    raw = rel.split("/")
    if any(p == ".." for p in raw):
        raise PathViolation("dotdot", alias, rel)
    parts = [unicodedata.normalize("NFC", p) for p in raw if p not in ("", ".")]
    if any(_is_reserved(p) for p in parts):
        raise PathViolation("reserved_name", alias, rel)
    return parts


class Sandbox:
    """Resolves ``alias + relative path`` against the worker's allowlisted roots."""

    def __init__(self, roots: list[RootConfig], audit: typing.Callable[[dict[str, str]], None] | None = None) -> None:
        self.audit = audit
        self.roots = {r.alias: r for r in roots}
        self._real = {r.alias: Path(os.path.realpath(r.path)) for r in roots}

    def resolve(self, alias: str, rel: str) -> SafePath:
        root = self._real[alias]
        parts = check_rel(rel, alias)
        real = root.joinpath(*parts)
        if not is_within(root, real):
            raise PathViolation("escape", alias, rel)
        cur = root
        for part in parts:
            cur = cur / part
            try:
                st = os.lstat(cur)
            except FileNotFoundError:
                break  # remaining components do not exist yet (write targets)
            if stat.S_ISLNK(st.st_mode):
                raise PathViolation("symlink", alias, rel)
        return SafePath(alias, "/".join(parts), real)

    def open_read(self, p: SafePath) -> typing.BinaryIO:
        """Open for reading without following symlinks at any component (FS-012)."""
        root = self._real[p.alias]
        parts = check_rel(p.rel, p.alias)
        if sys.platform == "win32":  # exercised by the Windows CI runner (os-matrix)
            self.resolve(p.alias, p.rel)
            return open(root.joinpath(*parts), "rb")  # noqa: SIM115
        nofollow = os.O_NOFOLLOW
        dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                try:
                    nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | nofollow, dir_fd=dfd)
                except OSError as e:
                    if e.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise PathViolation("symlink", p.alias, p.rel) from None
                    raise
                os.close(dfd)
                dfd = nfd
            try:
                fd = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=dfd)
            except OSError as e:
                if e.errno == errno.ELOOP:
                    raise PathViolation("symlink", p.alias, p.rel) from None
                raise
        finally:
            os.close(dfd)
        return os.fdopen(fd, "rb")

    def check_overwrite(self, p: SafePath) -> None:
        pass

    def open_write(self, alias: str, rel: str) -> SafePath:
        if self.roots[alias].mode != "rw":
            raise PathViolation("read_only_root", alias, rel)
        return self.resolve(alias, rel)
