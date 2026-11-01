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
        d.rectangle(
            [x0, y0, x0 + rnd.randrange(4, 30), y0 + rnd.randrange(4, 30)],
            fill=tuple(rnd.randrange(256) for _ in range(3)),
        )
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
        rows.append(
            [
                str(f.relative_to(PHOTOS)),
                "generated:tools/gen_fixtures.py",
                "OpenPhotoAgent contributors",
                "CC0-1.0",
                hashlib.sha256(f.read_bytes()).hexdigest(),
            ]
        )
    with (PHOTOS / "LICENSES.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "source", "author", "license", "sha256"])
        w.writerows(rows)
    print(f"{len(rows)} images")


# ---- slice 5 sets (all project-generated, CC0) -------------------------------------------------------
import io  # noqa: E402
import struct  # noqa: E402


def _jpeg_bytes(seed: int, size: tuple[int, int] = (64, 48), exif=None) -> bytes:  # type: ignore[no-untyped-def]
    b = io.BytesIO()
    _img(seed, size).save(b, "JPEG", quality=85, **({"exif": exif} if exif is not None else {}))
    return b.getvalue()


def gen_orientation() -> list[Path]:
    """img:orientation — EXIF orientation 1..8 on an asymmetric image (red top-left marker)."""
    out = PHOTOS / "orientation"
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for o in range(1, 9):
        im = Image.new("RGB", (80, 40), (20, 20, 20))
        ImageDraw.Draw(im).rectangle([0, 0, 19, 9], fill=(255, 0, 0))
        ex = Image.Exif()
        ex[0x0112] = o
        p = out / f"orient_{o}.jpg"
        im.save(p, "JPEG", quality=90, exif=ex)
        files.append(p)
    return files


def gen_heic() -> list[Path]:
    """img:heic — HEIC encoded with pillow-heif (portrait orientation, plain, 2 sizes)."""
    import pillow_heif

    pillow_heif.register_heif_opener()
    out = PHOTOS / "heic"
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for i, (size, orient) in enumerate([((96, 64), 1), ((96, 64), 6), ((128, 96), 1)]):
        im = _img(3000 + i, size)
        ex = _exif(datetime(2024, 5, 1 + i, 8, 0), "+08:00", "Apple", "iPhone 15")
        ex[0x0112] = orient
        p = out / f"IMG_{i + 1:04d}.HEIC"
        im.save(p, format="HEIF", quality=80, exif=ex.tobytes())
        files.append(p)
    return files


def gen_corrupt() -> list[Path]:
    out = PHOTOS / "corrupt"
    out.mkdir(parents=True, exist_ok=True)
    good = _jpeg_bytes(4000)
    (out / "truncated.jpg").write_bytes(good[: len(good) // 3])
    (out / "empty.jpg").write_bytes(b"")
    (out / "text.jpg").write_text("this is not an image\n", encoding="utf-8")
    ex = _exif(datetime(2024, 1, 1), "+08:00")
    bad = bytearray(_jpeg_bytes(4001, exif=ex))
    i = bad.find(b"Exif\x00\x00")
    bad[i + 10 : i + 18] = b"\xff" * 8  # break the TIFF IFD offsets; the image data stays readable
    (out / "bad_exif.jpg").write_bytes(bytes(bad))
    return sorted(out.iterdir())


def gen_timezone() -> list[Path]:
    out = PHOTOS / "timezone"
    out.mkdir(parents=True, exist_ok=True)

    def gps_ifd(lat: float, lon: float) -> dict:  # type: ignore[type-arg]
        def dms(v: float) -> tuple:  # type: ignore[type-arg]
            v = abs(v)
            d = int(v)
            m = int((v - d) * 60)
            s = round(((v - d) * 60 - m) * 60, 2)
            return (d, m, s)

        return {1: "N" if lat >= 0 else "S", 2: dms(lat), 3: "E" if lon >= 0 else "W", 4: dms(lon)}

    cases = {
        "offset_plus9.jpg": (datetime(2025, 3, 1, 10, 0), "+09:00", None),
        "gps_tokyo.jpg": (datetime(2025, 3, 1, 10, 0), None, (35.6812, 139.7671)),
        "local_assumed.jpg": (datetime(2025, 3, 1, 10, 0), None, None),
        "garbage_zero.jpg": ("0000:00:00 00:00:00", None, None),
        "garbage_month.jpg": ("2099:13:45 00:00:00", None, None),
        "garbage_empty.jpg": ("", None, None),
    }
    for name, (dt, off, gps) in cases.items():
        ex = Image.Exif()
        ifd = ex.get_ifd(EXIF_IFD)
        ifd[0x9003] = dt if isinstance(dt, str) else dt.strftime("%Y:%m:%d %H:%M:%S")
        if off:
            ifd[0x9011] = off
        if gps:
            g = ex.get_ifd(0x8825)
            g.update(gps_ifd(*gps))
        _img(5000 + len(name)).save(out / name, "JPEG", quality=85, exif=ex)
    return sorted(out.iterdir())


def _tiff_with_preview(preview: bytes | None) -> bytes:
    """Minimal little-endian TIFF/DNG-style container: IFD0 (tiny raw strip) + IFD1 with an embedded JPEG."""
    entries0 = [(254, 4, 1, 1), (256, 3, 1, 4), (257, 3, 1, 4), (258, 3, 1, 8), (259, 3, 1, 1), (262, 3, 1, 32803)]
    raw = b"\x10" * 16
    header = b"II*\x00" + struct.pack("<I", 8)
    n0 = len(entries0) + 2  # + StripOffsets + StripByteCounts
    ifd0_size = 2 + n0 * 12 + 4
    ifd1_off = 8 + ifd0_size
    ifd1_entries = 2 if preview else 0
    ifd1_size = 2 + ifd1_entries * 12 + 4
    raw_off = ifd1_off + ifd1_size
    jpg_off = raw_off + len(raw)
    b = bytearray(header)
    b += struct.pack("<H", n0)
    for tag, typ, cnt, val in entries0:
        b += struct.pack("<HHII", tag, typ, cnt, val)
    b += struct.pack("<HHII", 273, 4, 1, raw_off) + struct.pack("<HHII", 279, 4, 1, len(raw))
    b += struct.pack("<I", ifd1_off if preview else 0)
    if preview:
        b += (
            struct.pack("<H", 2)
            + struct.pack("<HHII", 513, 4, 1, jpg_off)
            + struct.pack("<HHII", 514, 4, 1, len(preview))
        )
        b += struct.pack("<I", 0)
    else:
        b += struct.pack("<H", 0) + struct.pack("<I", 0)
    b += raw
    if preview:
        b += preview
    return bytes(b)


def gen_raw() -> list[Path]:
    """img:raw — synthetic RAW containers (raw.pixls.us is unreachable from CI/cloud; real samples: nightly)."""
    out = PHOTOS / "raw"
    out.mkdir(parents=True, exist_ok=True)
    prev = lambda s: _jpeg_bytes(s, (160, 120), _exif(datetime(2023, 9, 9, 9, 9), "+02:00", "Synth", "RAW-1"))  # noqa: E731
    (out / "sample.dng").write_bytes(_tiff_with_preview(prev(6000)))
    (out / "sample.nef").write_bytes(_tiff_with_preview(prev(6001)))
    (out / "sample.arw").write_bytes(_tiff_with_preview(prev(6002)))
    (out / "no_preview.dng").write_bytes(_tiff_with_preview(None))
    raf_jpg = prev(6003)
    raf = bytearray(b"FUJIFILMCCD-RAW 0201FF383501" + b"\x00" * 64)
    raf = raf[:84] + struct.pack(">II", 100, len(raf_jpg)) + b"\x00" * 8
    raf += b"\x00" * (100 - len(raf)) + raf_jpg
    (out / "sample.raf").write_bytes(bytes(raf))
    cr3_jpg = prev(6004)
    prvw = b"PRVW" + b"\x00" * 12 + cr3_jpg
    uuid_box = struct.pack(">I", 8 + 16 + len(prvw)) + b"uuid" + bytes(16) + prvw
    ftyp = struct.pack(">I", 24) + b"ftypcrx " + b"\x00\x00\x00\x01" + b"crx isom"
    (out / "sample.cr3").write_bytes(ftyp + uuid_box)
    return sorted(out.iterdir())


def gen_video() -> list[Path]:
    out = PHOTOS / "video"
    out.mkdir(parents=True, exist_ok=True)
    ftyp = struct.pack(">I", 20) + b"ftypisom" + b"\x00\x00\x02\x00" + b"isom"
    (out / "clip.mp4").write_bytes(ftyp + struct.pack(">I", 8) + b"mdat")
    (out / "clip.mov").write_bytes(struct.pack(">I", 20) + b"ftypqt  " + b"\x00\x00\x02\x00" + b"qt  " + b"\x00" * 8)
    return sorted(out.iterdir())


def main_slice5() -> None:
    rows = []
    for f in gen_orientation() + gen_heic() + gen_corrupt() + gen_timezone() + gen_raw() + gen_video():
        rows.append(
            [
                str(f.relative_to(PHOTOS)),
                "generated:tools/gen_fixtures.py",
                "OpenPhotoAgent contributors",
                "CC0-1.0",
                hashlib.sha256(f.read_bytes()).hexdigest(),
            ]
        )
    with (PHOTOS / "LICENSES.csv").open("a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    print(f"{len(rows)} slice-5 files")


if __name__ == "__main__":
    import sys

    main_slice5() if "--slice5" in sys.argv else main()
