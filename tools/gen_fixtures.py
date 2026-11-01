#!/usr/bin/env python3
"""Generate project-owned CC0 test images (docs/09 §3.6). Deterministic; re-running must not change bytes
for an unchanged Pillow/libjpeg version. Outputs fixtures/photos/<set>/ and appends LICENSES.csv rows."""

from __future__ import annotations

import csv
import hashlib
import random
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
PHOTOS = ROOT / "fixtures" / "photos"
EXIF_IFD = 0x8769


def _img(seed: int, size: tuple[int, int] = (64, 48)) -> Image.Image:
    rnd = random.Random(seed)
    im = Image.new("RGB", size, tuple(rnd.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(6):
        x0, y0 = rnd.randrange(size[0]), rnd.randrange(size[1])
        d.rectangle([x0, y0, x0 + rnd.randrange(4, 30), y0 + rnd.randrange(4, 30)],
                    fill=tuple(rnd.randrange(256) for _ in range(3)))
    return im


def _exif(dt: datetime, offset: str | None, make: str = "OPA", model: str = "Synth-1"):  # type: ignore[no-untyped-def]
    ex = Image.Exif()
    ex[0x010F] = make
    ex[0x0110] = model
    ifd = ex.get_ifd(EXIF_IFD)
    ifd[0x9003] = dt.strftime("%Y:%m:%d %H:%M:%S")
    if offset:
        ifd[0x9011] = offset
    return ex


def gen_basic(n: int = 20) -> list[Path]:
    out = PHOTOS / "basic"
    out.mkdir(parents=True, exist_ok=True)
    base = datetime(2023, 1, 15, 9, 30)
    files = []
    for i in range(n):
        dt = base + timedelta(days=37 * i, hours=i)
        p = out / f"IMG_{i + 1:04d}.jpg"
        _img(1000 + i).save(p, "JPEG", quality=85, exif=_exif(dt, "+08:00"))
        files.append(p)
    return files


def gen_no_exif(n: int = 10) -> list[Path]:
    out = PHOTOS / "no-exif"
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(n):
        p = out / f"NOEXIF_{i + 1:04d}.jpg"
        _img(2000 + i).save(p, "JPEG", quality=85)
        files.append(p)
    return files


def main() -> None:
    rows = []
    for f in gen_basic() + gen_no_exif():
        rows.append([str(f.relative_to(PHOTOS)), "generated:tools/gen_fixtures.py", "OpenPhotoAgent contributors",
                     "CC0-1.0", hashlib.sha256(f.read_bytes()).hexdigest()])
    with (PHOTOS / "LICENSES.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "source", "author", "license", "sha256"])
        w.writerows(rows)
    print(f"{len(rows)} images")


if __name__ == "__main__":
    main()
