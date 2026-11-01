"""Decoding, RAW previews, thumbnails, ThumbHash and perceptual hashes (docs/02 §4.1 stage 3, §10.5; 10 §C)."""

from __future__ import annotations

import io
import math
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

RAW_EXT = {".dng", ".nef", ".arw", ".cr2", ".cr3", ".raf", ".orf", ".rw2", ".pef", ".srw", ".nrw"}
THUMB_SIZES = (256, 512)
_HEIF = False


class RawPreviewMissing(Exception):
    pass


def register_heif() -> None:
    global _HEIF
    if not _HEIF:
        import pillow_heif

        pillow_heif.register_heif_opener()
        _HEIF = True


def _tiff_previews(data: bytes) -> list[tuple[int, int]]:
    """(offset, length) of JPEGs referenced from TIFF IFDs (IFD chain + SubIFDs): DNG, NEF, ARW, CR2, ORF..."""
    if data[:2] not in (b"II", b"MM"):
        return []
    e = "<" if data[:2] == b"II" else ">"
    out: list[tuple[int, int]] = []
    seen: set[int] = set()
    todo = [struct.unpack(e + "I", data[4:8])[0]]
    while todo:
        off = todo.pop()
        if off in seen or off <= 0 or off + 2 > len(data):
            continue
        seen.add(off)
        n = struct.unpack(e + "H", data[off : off + 2])[0]
        tags: dict[int, int] = {}
        for i in range(n):
            p = off + 2 + 12 * i
            if p + 12 > len(data):
                break
            tag, typ, cnt = struct.unpack(e + "HHI", data[p : p + 8])
            val = struct.unpack(e + ("H" if typ == 3 else "I"), data[p + 8 : p + (10 if typ == 3 else 12)])[0]
            tags[tag] = val
            if tag == 330:  # SubIFDs
                if cnt == 1:
                    todo.append(val)
                else:
                    todo.extend(struct.unpack(e + "I" * cnt, data[val : val + 4 * cnt]))
        if 513 in tags and 514 in tags:
            out.append((tags[513], tags[514]))
        nxt = off + 2 + 12 * n
        if nxt + 4 <= len(data):
            todo.append(struct.unpack(e + "I", data[nxt : nxt + 4])[0])
    return out


def raw_preview(data: bytes, ext: str) -> bytes:
    """Largest embedded JPEG preview of a RAW file; raises RawPreviewMissing (-> DECODE_RAW_MISSING)."""
    cands: list[tuple[int, int]] = []
    if ext == ".raf" and data.startswith(b"FUJIFILMCCD-RAW"):
        off, ln = struct.unpack(">II", data[84:92])
        cands.append((off, ln))
    elif ext == ".cr3":
        i = data.find(b"PRVW")
        j = data.find(b"\xff\xd8\xff", i if i >= 0 else 0)
        k = data.find(b"\xff\xd9", j)
        if j >= 0 and k > j:
            cands.append((j, k + 2 - j))
    else:
        cands.extend(_tiff_previews(data))
    good = [(o, n) for o, n in cands if 0 < o < len(data) and n > 4 and data[o : o + 2] == b"\xff\xd8"]
    if not good:
        raise RawPreviewMissing(ext)
    o, n = max(good, key=lambda x: x[1])
    return data[o : o + n]


def open_image(path: Path) -> Image.Image:
    """Open any supported photo (JPEG/PNG/WebP/TIFF, HEIC via pillow-heif, RAW via embedded preview)."""
    ext = path.suffix.lower()
    if ext in RAW_EXT:
        return Image.open(io.BytesIO(raw_preview(path.read_bytes(), ext)))
    register_heif()
    return Image.open(path)


def oriented(im: Image.Image) -> Image.Image:
    return ImageOps.exif_transpose(im) or im


def write_thumbnails(im: Image.Image, dst_dir: Path, content_hash: str) -> list[str]:
    """t256/t512 WebP, EXIF orientation applied, never upscaled (02 §4.1 stage 3)."""
    d = dst_dir / content_hash[:2]
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for size in THUMB_SIZES:
        t = im.copy()
        t.thumbnail((size, size), Image.Resampling.LANCZOS)
        t.convert("RGB").save(d / f"{content_hash}.t{size}.webp", "WEBP", quality=82)
        out.append(f"t{size}")
    return out


def phash(im: Image.Image) -> str:
    """DCT perceptual hash (64 bits, hex): 32x32 grey -> DCT-II -> 8x8 low frequencies vs median."""
    g = np.asarray(im.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    n = 32
    k = np.arange(n)
    c = np.cos(np.pi / n * (k[:, None]) * (k[None, :] + 0.5))
    d = c @ g @ c.T
    low = d[:8, :8].flatten()
    bits = low > np.median(low)
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def dhash(im: Image.Image) -> str:
    g = np.asarray(im.convert("L").resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def thumbhash_encode(im: Image.Image) -> bytes:
    """ThumbHash (Evan Wallace, MIT) — port of rgbaToThumbHash; image is downscaled to <= 100x100 first."""
    im = im.convert("RGBA")
    im.thumbnail((100, 100))
    w, h = im.size
    rgba = im.tobytes()
    avg_r = avg_g = avg_b = avg_a = 0.0
    for i in range(w * h):
        j = 4 * i
        alpha = rgba[j + 3] / 255
        avg_r += alpha / 255 * rgba[j]
        avg_g += alpha / 255 * rgba[j + 1]
        avg_b += alpha / 255 * rgba[j + 2]
        avg_a += alpha
    if avg_a:
        avg_r, avg_g, avg_b = avg_r / avg_a, avg_g / avg_a, avg_b / avg_a
    has_alpha = avg_a < w * h
    l_limit = 5 if has_alpha else 7
    lx = max(1, round(l_limit * w / max(w, h)))
    ly = max(1, round(l_limit * h / max(w, h)))
    lum, pp, qq, aa = [], [], [], []
    for i in range(w * h):
        j = 4 * i
        alpha = rgba[j + 3] / 255
        r = avg_r * (1 - alpha) + alpha / 255 * rgba[j]
        g = avg_g * (1 - alpha) + alpha / 255 * rgba[j + 1]
        b = avg_b * (1 - alpha) + alpha / 255 * rgba[j + 2]
        lum.append((r + g + b) / 3)
        pp.append((r + g) / 2 - b)
        qq.append(r - g)
        aa.append(alpha)

    def channel(ch: list[float], nx: int, ny: int) -> tuple[float, list[float], float]:
        dc, ac, scale = 0.0, [], 0.0
        for cy in range(ny):
            cx = 0
            while cx * ny < nx * (ny - cy):
                fx = [math.cos(math.pi / w * cx * (x + 0.5)) for x in range(w)]
                f = 0.0
                for y in range(h):
                    fy = math.cos(math.pi / h * cy * (y + 0.5))
                    row = y * w
                    for x in range(w):
                        f += ch[x + row] * fx[x] * fy
                f /= w * h
                if cx or cy:
                    ac.append(f)
                    scale = max(scale, abs(f))
                else:
                    dc = f
                cx += 1
        if scale:
            ac = [0.5 + 0.5 / scale * v for v in ac]
        return dc, ac, scale

    l_dc, l_ac, l_scale = channel(lum, max(3, lx), max(3, ly))
    p_dc, p_ac, p_scale = channel(pp, 3, 3)
    q_dc, q_ac, q_scale = channel(qq, 3, 3)
    a_dc, a_ac, a_scale = channel(aa, 5, 5) if has_alpha else (0.0, [], 0.0)
    landscape = w > h
    h24 = (
        round(63 * l_dc)
        | (round(31.5 + 31.5 * p_dc) << 6)
        | (round(31.5 + 31.5 * q_dc) << 12)
        | (round(31 * l_scale) << 18)
        | (int(has_alpha) << 23)
    )
    h16 = (ly if landscape else lx) | (round(63 * p_scale) << 3) | (round(63 * q_scale) << 9) | (int(landscape) << 15)
    out = [h24 & 255, (h24 >> 8) & 255, h24 >> 16, h16 & 255, h16 >> 8]
    start = 6 if has_alpha else 5
    if has_alpha:
        out.append(round(15 * a_dc) | (round(15 * a_scale) << 4))
    idx = 0
    for ac in [l_ac, p_ac, q_ac] + ([a_ac] if has_alpha else []):
        for f in ac:
            pos = start + (idx >> 1)
            while len(out) <= pos:
                out.append(0)
            out[pos] |= round(15 * f) << ((idx & 1) << 2)
            idx += 1
    return bytes(out)
