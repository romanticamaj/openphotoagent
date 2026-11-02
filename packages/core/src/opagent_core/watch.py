"""Watch-folder decisions (05 §5; 09b §6), pure: time is passed in, sizes come from a callable.

* A file is handed on only when its folder has been quiet for ``debounce_s`` (a phone sync writes many files in a
  row) **and** its size has not changed for ``stable_s`` (a long copy is not read half-written). Each file once.
* Many ready files become one batch; a large import runs at low priority instead of crowding interactive work.
* Network file systems do not deliver native change events reliably: they are polled.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field

LARGE_BATCH = 500  # more than this many new files in one go → priority low (05 §5)
MAX_BATCH = 10_000
NETWORK_FS = frozenset(
    {"cifs", "smb", "smb2", "smb3", "smbfs", "nfs", "nfs4", "afpfs", "webdav", "davfs", "9p", "fuse.sshfs", "sshfs"}
)


def mode_for(fstype: str) -> str:
    """``native`` change events, or ``poll`` (periodic incremental scan) on a network file system."""
    t = (fstype or "").lower()
    return "poll" if t in NETWORK_FS or t.startswith(("fuse.sshfs", "nfs", "smb", "cifs")) else "native"


def fstype_of(path: str, mounts: str) -> str:
    """File system type of ``path`` from a ``/proc/mounts``-style table (the longest matching mount point)."""
    best, kind = "", ""
    p = path.rstrip("/") or "/"
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mp = parts[1].replace("\\040", " ").rstrip("/") or "/"
        inside = mp == "/" or p == mp or p.startswith(mp + "/")
        if inside and len(mp) >= len(best):
            best, kind = mp, parts[2]
    return kind


@dataclass(frozen=True)
class Batch:
    paths: tuple[str, ...]
    priority: str

    @property
    def count(self) -> int:
        return len(self.paths)


def batches(paths: list[str]) -> list[Batch]:
    out = []
    for i in range(0, len(paths), MAX_BATCH):
        chunk = tuple(paths[i : i + MAX_BATCH])
        out.append(Batch(chunk, "low" if len(paths) > LARGE_BATCH else "normal"))
    return out


@dataclass
class _Seen:
    size: int | None = None
    size_since: float = 0.0


@dataclass
class Debouncer:
    debounce_s: float
    stable_s: float
    _files: dict[str, _Seen] = field(default_factory=dict)
    _folder_event: dict[str, float] = field(default_factory=dict)

    def event(self, path: str, now: float) -> None:
        self._files.setdefault(path, _Seen(size_since=now))
        folder = posixpath.dirname(path)
        self._folder_event[folder] = max(now, self._folder_event.get(folder, now))

    def pending(self) -> int:
        return len(self._files)

    def ready(self, now: float, size_of: Callable[[str], int | None]) -> list[str]:
        out = []
        for path in sorted(self._files):
            seen = self._files[path]
            size = size_of(path)
            if size is None:  # removed (or renamed away) before it settled
                del self._files[path]
                continue
            if size != seen.size:
                seen.size, seen.size_since = size, now
            quiet = now - self._folder_event.get(posixpath.dirname(path), now) >= self.debounce_s
            if quiet and now - seen.size_since >= self.stable_s:
                out.append(path)
                del self._files[path]
        busy = {posixpath.dirname(p) for p in self._files}
        self._folder_event = {f: t for f, t in self._folder_event.items() if f in busy}
        return out
