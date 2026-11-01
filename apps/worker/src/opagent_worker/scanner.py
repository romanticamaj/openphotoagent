"""Scanner: walk + hash + metadata (docs/02 §4.1, 09a §4)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from functools import lru_cache
from pathlib import Path

import blake3
from opagent_core.path_template import DateSource
from opagent_fs.safepath import Sandbox
from PIL import Image

EXIF_IFD = 0x8769
DATETIME_ORIGINAL = 0x9003
OFFSET_TIME_ORIGINAL = 0x9011
CHUNK = 1 << 20


@dataclass(frozen=True)
class PhotoMeta:
    taken_at: datetime | None
    date_source: DateSource | None


def _expand_braces(pat: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", pat)
    if not m:
        return [pat]
    return [x for alt in m.group(1).split(",") for x in _expand_braces(pat[: m.start()] + alt + pat[m.end() :])]


@lru_cache(maxsize=256)
def _glob_re(pat: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def _matches(rel: str, patterns: list[str]) -> bool:
    return any(_glob_re(x).match(rel) for p in patterns for x in _expand_braces(p))


def list_files(sb: Sandbox, alias: str, sub: str, include: list[str], exclude: list[str]) -> list[str]:
    """Relative paths (to the root) of regular files under ``sub`` matching include and not exclude."""
    base = sb.resolve(alias, sub)
    root_real = sb.resolve(alias, "").real
    out: list[str] = []
    for dirpath, dirnames, files in os.walk(base.real, followlinks=False):
        dirnames.sort()
        for f in sorted(files):
            full = Path(dirpath, f)
            if full.is_symlink() or not full.is_file():
                continue
            rel = full.relative_to(root_real).as_posix()
            if _matches(rel, include) and not _matches(rel, exclude):
                out.append(rel)
    return out


def content_hash(path: Path) -> str:
    h = blake3.blake3()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _parse_offset(s: str) -> timezone | None:
    m = re.fullmatch(r"([+-])(\d{2}):(\d{2})", s.strip())
    if not m:
        return None
    sign = 1 if m.group(1) == "+" else -1
    return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))


def read_meta(path: Path, mtime: datetime, tz: tzinfo) -> PhotoMeta:
    raw_dt = offset = None
    try:
        with Image.open(path) as im:
            ifd = im.getexif().get_ifd(EXIF_IFD)
            raw_dt = ifd.get(DATETIME_ORIGINAL)
            offset = ifd.get(OFFSET_TIME_ORIGINAL)
    except (OSError, ValueError, SyntaxError):
        pass
    if isinstance(raw_dt, str):
        try:
            naive = datetime.strptime(raw_dt.strip(), "%Y:%m:%d %H:%M:%S")  # noqa: DTZ007 - tz attached below
        except ValueError:
            naive = None
        if naive is not None:
            off = _parse_offset(offset) if isinstance(offset, str) else None
            if off is not None:
                return PhotoMeta(naive.replace(tzinfo=off), "exif_offset")
            return PhotoMeta(naive.replace(tzinfo=tz), "exif_local_assumed")
    return PhotoMeta(mtime, "mtime")


@dataclass
class ScanEntry:
    rel: str
    size: int
    mtime: datetime
    content_hash: str
    taken_at: datetime | None
    date_source: DateSource | None


@dataclass
class Skipped:
    rel: str
    reason: str


@dataclass
class ScanResult:
    entries: list[ScanEntry]
    skipped: list[Skipped]
    bytes_read: int = 0


class HashCache:
    def __init__(self, path: Path) -> None:
        self.path = path


def scan(sb: Sandbox, alias: str, sub: str, include: list[str], exclude: list[str], tz: tzinfo,
         cache: HashCache | None = None, **kw: object) -> ScanResult:
    return ScanResult([], [])
