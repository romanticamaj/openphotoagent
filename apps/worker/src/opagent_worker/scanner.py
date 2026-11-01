"""Scanner: walk + hash + metadata (docs/02 §4.1, §10.5; 09a §4).

Only the worker reads files (invariant 1); every path goes through SafePath. Unchanged files are answered
from a local cache keyed by (abs path, size, mtime_ns, file id) so a rescan reads 0 bytes (SCAN-004).
"""

from __future__ import annotations

import base64
import errno
import json
import os
import re
import sqlite3
import stat as stat_mod
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Any

import blake3
from opagent_core.path_template import DateSource
from opagent_fs.safepath import Sandbox
from PIL import Image

from .imaging import RawPreviewMissing, dhash, open_image, oriented, phash, thumbhash_encode, write_thumbnails

__all__ = ["Image", "dhash", "phash", "thumbhash_encode"]

EXIF_IFD = 0x8769
GPS_IFD = 0x8825
DATETIME_ORIGINAL = 0x9003
OFFSET_TIME_ORIGINAL = 0x9011
CHUNK = 1 << 20
ALWAYS_EXCLUDE = [
    "**/.DS_Store",
    "**/Thumbs.db",
    "**/desktop.ini",
    "**/.opagent-trash/**",
    "**/.opagent-tmp-*",
    "**/@eaDir/**",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
]
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".3gp"}
PACKAGE_DIR_SUFFIXES = {".photoslibrary": "photos_library_package"}
# Windows cloud-file placeholders (OneDrive): RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE
PLACEHOLDER_ATTRS = 0x400000 | 0x40000 | 0x1000


@dataclass(frozen=True)
class PhotoMeta:
    taken_at: datetime | None
    date_source: DateSource | None


@dataclass
class ScanEntry:
    rel: str
    size: int
    mtime: datetime
    content_hash: str
    taken_at: datetime | None
    date_source: DateSource | None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Skipped:
    rel: str
    reason: str
    fix: str | None = None


def _is_placeholder(st: os.stat_result, path: str) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & PLACEHOLDER_ATTRS)


@dataclass
class ScanResult:
    entries: list[ScanEntry]
    skipped: list[Skipped]
    bytes_read: int = 0


def _expand_braces(pat: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", pat)
    if not m:
        return [pat]
    return [x for alt in m.group(1).split(",") for x in _expand_braces(pat[: m.start()] + alt + pat[m.end() :])]


@lru_cache(maxsize=512)
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
    files, _ = _walk(sb, alias, sub, include, exclude, [])
    return [r for r, _st in files]


def _walk(
    sb: Sandbox, alias: str, sub: str, include: list[str], exclude: list[str], exclude_subtrees: list[str]
) -> tuple[list[tuple[str, os.stat_result]], list[Skipped]]:
    base = sb.resolve(alias, sub)
    root_real = sb.resolve(alias, "").real
    files: list[tuple[str, os.stat_result]] = []
    skipped: list[Skipped] = []
    excl = ALWAYS_EXCLUDE + list(exclude)
    stack = [base.real]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                ents = sorted(it, key=lambda e: e.name)
        except PermissionError as err:
            # macOS TCC denies with EPERM: point at the doctor's folder-access fix (SCAN-015)
            fix = "fs.roots" if err.errno == errno.EPERM else None
            skipped.append(Skipped(Path(d).relative_to(root_real).as_posix(), "FS_PERMISSION_DENIED", fix))
            continue
        for e in reversed(ents):
            rel = unicodedata.normalize("NFC", Path(e.path).relative_to(root_real).as_posix())
            if e.is_symlink():
                skipped.append(Skipped(rel, "symlink"))
                continue
            if e.is_dir(follow_symlinks=False):
                if _matches(rel + "/x", excl):
                    continue
                if any(rel == t or rel.startswith(t.rstrip("/") + "/") for t in exclude_subtrees):
                    continue  # output target inside the source (SCAN-024)
                reason = next((r for suf, r in PACKAGE_DIR_SUFFIXES.items() if e.name.lower().endswith(suf)), None)
                if reason:
                    skipped.append(Skipped(rel, reason))
                    continue
                stack.append(Path(e.path))
                continue
            if _matches(rel, excl) or not _matches(rel, include):
                continue
            st = e.stat(follow_symlinks=False)
            if _is_placeholder(st, e.path):
                skipped.append(Skipped(rel, "cloud_placeholder"))  # never open: would trigger a download (SCAN-016)
                continue
            if Path(rel).suffix.lower() in VIDEO_EXT:
                skipped.append(Skipped(rel, "video_unsupported_v0_1"))
                continue
            files.append((rel, st))
    files.sort(key=lambda x: x[0])
    skipped.sort(key=lambda s: s.rel)
    return files, skipped


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


def _gps_deg(v: Any, ref: Any) -> float | None:
    try:
        d, m, s = (float(x) for x in v)
    except (TypeError, ValueError):
        return None
    deg = d + m / 60 + s / 3600
    return -deg if str(ref).upper() in ("S", "W") else deg


def _gps_tz(gps: dict[int, Any]) -> tzinfo | None:
    lat, lon = _gps_deg(gps.get(2), gps.get(1)), _gps_deg(gps.get(4), gps.get(3))
    if lat is None or lon is None:
        return None
    try:
        from zoneinfo import ZoneInfo

        import tzfpy

        name = tzfpy.get_tz(lon, lat)
        return ZoneInfo(name) if name else None
    except Exception:
        return None


def exif_of(path: Path) -> Any:
    with open_image(path) as im:
        return im.getexif()


def read_meta(path: Path, mtime: datetime, tz: tzinfo) -> PhotoMeta:
    """Capture date priority (SCAN-012): EXIF offset > GPS time zone > workspace tz; garbage -> mtime (SCAN-014)."""
    try:
        ex = exif_of(path)
        ifd = ex.get_ifd(EXIF_IFD)
        raw_dt, offset, gps = ifd.get(DATETIME_ORIGINAL), ifd.get(OFFSET_TIME_ORIGINAL), ex.get_ifd(GPS_IFD)
    except Exception:
        raw_dt = offset = gps = None
    naive = None
    if isinstance(raw_dt, str):
        try:
            naive = datetime.strptime(raw_dt.strip(), "%Y:%m:%d %H:%M:%S")  # noqa: DTZ007 - tz attached below
        except ValueError:
            naive = None
    if naive is None:
        return PhotoMeta(mtime, "mtime")
    off = _parse_offset(offset) if isinstance(offset, str) else None
    if off is not None:
        return PhotoMeta(naive.replace(tzinfo=off), "exif_offset")
    gtz = _gps_tz(dict(gps)) if gps else None
    if gtz is not None:
        aware = naive.replace(tzinfo=gtz)
        return PhotoMeta(aware.replace(tzinfo=timezone(aware.utcoffset() or timedelta(0))), "exif_gps_tz")
    return PhotoMeta(naive.replace(tzinfo=tz), "exif_local_assumed")


class HashCache:
    """Worker-local cache (abs path, size, mtime_ns, file id) -> scan entry (02 §4.1 stage 2)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS entry(k TEXT PRIMARY KEY, v TEXT NOT NULL)")

    @staticmethod
    def key(abs_path: Path, st: os.stat_result) -> str:
        return f"{abs_path}|{st.st_size}|{st.st_mtime_ns}|{st.st_ino}"

    def get(self, k: str) -> dict[str, Any] | None:
        r = self._db.execute("SELECT v FROM entry WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else None

    def put(self, k: str, v: dict[str, Any]) -> None:
        self._db.execute("INSERT OR REPLACE INTO entry(k, v) VALUES (?, ?)", (k, json.dumps(v)))
        self._db.commit()


def _entry_to_json(e: ScanEntry) -> dict[str, Any]:
    d = asdict(e)
    d["mtime"] = e.mtime.isoformat()
    d["taken_at"] = e.taken_at.isoformat() if e.taken_at else None
    return d


def _entry_from_json(d: dict[str, Any]) -> ScanEntry:
    return ScanEntry(
        rel=d["rel"],
        size=d["size"],
        mtime=datetime.fromisoformat(d["mtime"]),
        content_hash=d["content_hash"],
        taken_at=datetime.fromisoformat(d["taken_at"]) if d["taken_at"] else None,
        date_source=d["date_source"],
        extra=d.get("extra") or {},
    )


def scan(
    sb: Sandbox,
    alias: str,
    sub: str,
    include: list[str],
    exclude: list[str],
    tz: tzinfo,
    cache: HashCache | None = None,
    exclude_subtrees: list[str] | None = None,
    thumbs_dir: Path | None = None,
    reporter: Any = None,
    previous_total: int | None = None,
) -> ScanResult:
    """enumerate (indeterminate unless a previous total is known) -> hash/exif/thumbnails (determinate)."""
    if reporter is not None:
        reporter.start("scan", previous_total)
        reporter.phase("enumerate")
    files, skipped = _walk(sb, alias, sub, include, exclude, exclude_subtrees or [])
    if reporter is not None:
        reporter.progress(len(files), None)
        reporter.phase("hash")
    entries: list[ScanEntry] = []
    bytes_read = 0
    total = len(files)
    for n, (rel, st) in enumerate(files, start=1):
        sp = sb.resolve(alias, rel)
        k = HashCache.key(sp.real, st)
        cached = cache.get(k) if cache is not None else None
        if cached is not None:
            entries.append(_entry_from_json(cached))
        else:
            e, reason = _scan_one(sp.real, sp.rel, st, tz, thumbs_dir)
            if e is None:
                skipped.append(Skipped(rel, reason or "DECODE_FAILED"))
            else:
                bytes_read += st.st_size
                if cache is not None:
                    cache.put(k, _entry_to_json(e))
                entries.append(e)
        if reporter is not None:
            reporter.progress(n, total)
    skipped.sort(key=lambda s: s.rel)
    if reporter is not None:
        reporter.finish("succeeded")
    return ScanResult(entries, skipped, bytes_read)


def _scan_one(
    path: Path, rel: str, st: os.stat_result, tz: tzinfo, thumbs_dir: Path | None
) -> tuple[ScanEntry | None, str | None]:
    if st.st_size == 0 or not stat_mod.S_ISREG(st.st_mode):
        return None, "DECODE_FAILED"
    try:
        with open_image(path) as raw_im:
            raw_im.load()
            im = oriented(raw_im)
            mtime = datetime.fromtimestamp(st.st_mtime, tz=tz)
            h = content_hash(path)
            meta = read_meta(path, mtime, tz)
            extra: dict[str, Any] = {
                "width": im.width,
                "height": im.height,
                "thumbhash": base64.b64encode(thumbhash_encode(im)).decode(),
                "phash": phash(im),
                "dhash": dhash(im),
            }
            if thumbs_dir is not None:
                extra["thumbs"] = write_thumbnails(im, Path(thumbs_dir), h)
    except RawPreviewMissing:
        return None, "DECODE_RAW_MISSING"
    except Exception:
        return None, "DECODE_FAILED"
    return ScanEntry(
        rel=rel,
        size=st.st_size,
        mtime=mtime,
        content_hash=h,
        taken_at=meta.taken_at,
        date_source=meta.date_source,
        extra=extra,
    ), None
