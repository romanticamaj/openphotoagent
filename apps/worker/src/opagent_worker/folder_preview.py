"""What a folder holds, before anything is imported (ADR-0029). Reads names and sizes, and the headers of a few photos;
nothing here writes, and every path goes through the sandbox (invariants 1, 2)."""

from __future__ import annotations

import base64
import io
import os
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

from opagent_fs.safepath import Sandbox
from PIL import Image

from .imaging import open_image, oriented
from .scanner import VIDEO_EXT, _is_placeholder, read_meta

# what a default source scans (routes_public.DEFAULT_INCLUDE): a count here has to match what importing will find
PHOTO_EXT = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".webp",
    ".tif",
    ".tiff",
    ".dng",
    ".cr3",
    ".nef",
    ".arw",
    ".raf",
}
NOT_CONTENT = {"thumbs.db", "desktop.ini", ".ds_store"}
DATE_SAMPLES = 40  # EXIF for every file would take minutes on a large folder
THUMB_SIDE = 256


def _spread(items: list[Any], n: int) -> list[Any]:
    if len(items) <= n:
        return items
    return [items[i * len(items) // n] for i in range(n)]


def folder_preview(
    sb: Sandbox, alias: str, path: str, tz: tzinfo, samples: int = 12, max_files: int = 50_000
) -> dict[str, Any]:
    base = sb.resolve(alias, path)
    root = sb.resolve(alias, "").real
    photos: list[tuple[str, os.stat_result]] = []
    cloud = videos = others = size = 0
    per_child: dict[str, dict[str, int]] = {}
    truncated = False
    stack: list[tuple[Path, str | None]] = [(base.real, None)]
    while stack and not truncated:
        d, child = stack.pop()
        try:
            with os.scandir(d) as it:
                ents = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for e in ents:
            if e.is_symlink() or e.is_junction() or e.name.startswith("."):
                continue
            if e.is_dir(follow_symlinks=False):
                name = child or e.name
                per_child.setdefault(name, {"photos": 0, "videos": 0})
                stack.append((Path(e.path), name))
                continue
            if e.name.lower() in NOT_CONTENT:
                continue
            if len(photos) + cloud + videos + others >= max_files:
                truncated = True
                break
            st = e.stat(follow_symlinks=False)
            ext = Path(e.name).suffix.lower()
            if ext in PHOTO_EXT:
                size += st.st_size
                if _is_placeholder(st, e.path):  # never open a cloud file: it would download
                    cloud += 1
                else:
                    photos.append((Path(e.path).relative_to(root).as_posix(), st))
                if child:
                    per_child[child]["photos"] += 1
            elif ext in VIDEO_EXT:
                videos += 1
                size += st.st_size
                if child:
                    per_child[child]["videos"] += 1
            else:
                others += 1
    photos.sort(key=lambda x: x[0])
    taken = []
    for rel, st in _spread(photos, DATE_SAMPLES):
        mtime = datetime.fromtimestamp(st.st_mtime, tz=tz)
        when = read_meta(sb.resolve(alias, rel).real, mtime, tz).taken_at
        taken.append(when or mtime)
    return {
        "photos": len(photos) + cloud,
        "videos": videos,
        "others": others,
        "bytes": size,
        "truncated": truncated,
        "subfolders": [{"name": k, **v} for k, v in sorted(per_child.items())],
        "samples": [rel for rel, _ in _spread(photos, samples)],
        "taken_from": min(taken).isoformat() if taken else None,
        "taken_to": max(taken).isoformat() if taken else None,
    }


def folder_thumb(sb: Sandbox, alias: str, rel: str) -> dict[str, Any]:
    sp = sb.resolve(alias, rel)
    if sp.real.suffix.lower() not in PHOTO_EXT:
        raise ValueError("not a photo")
    with open_image(sp.real) as raw:
        raw.draft("RGB", (THUMB_SIDE * 2, THUMB_SIDE * 2))  # JPEG decodes at a fraction of full size
        im: Image.Image = oriented(raw).convert("RGB")
        im.thumbnail((THUMB_SIDE, THUMB_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
    return {"thumbnail_b64": base64.b64encode(buf.getvalue()).decode()}
