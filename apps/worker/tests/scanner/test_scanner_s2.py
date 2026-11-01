import errno
import os
import shutil
import unicodedata
from datetime import timedelta, timezone
from pathlib import Path

import numpy as np
import opagent_worker.scanner as scanner_mod
import pytest
from opagent_fs.safepath import RootConfig, Sandbox
from opagent_worker.scanner import dhash, phash, scan, thumbhash_encode
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[4]
FX = ROOT / "fixtures/photos"
TW = timezone(timedelta(hours=8))
INC = ["**/*"]


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.astype(np.float64), b.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    ma, mb = a.mean(), b.mean()
    va, vb, cov = a.var(), b.var(), ((a - ma) * (b - mb)).mean()
    return ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma**2 + mb**2 + c1) * (va + vb + c2))


@pytest.fixture
def sb(sandbox_tree):
    return Sandbox([RootConfig(**r) for r in sandbox_tree.roots])


def put(tree, rel, src):
    p = tree.photos / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, p)
    return p


@pytest.mark.integration
@pytest.mark.tc("SCAN-008")
def test_scan_008_heic_decoded_with_thumbnails(sb, sandbox_tree, tmp_path):
    import pillow_heif

    pillow_heif.register_heif_opener()
    for f in sorted((FX / "heic").iterdir()):
        put(sandbox_tree, f.name, f)
    res = scan(sb, "photos", "", INC, [], tz=TW, thumbs_dir=tmp_path / "thumbs")
    assert len(res.entries) == 3 and res.skipped == []
    for e in res.entries:
        assert e.date_source == "exif_offset"
        for size in (256, 512):
            t = tmp_path / "thumbs" / e.content_hash[:2] / f"{e.content_hash}.t{size}.webp"
            assert t.exists()
        ref = ImageOps.exif_transpose(Image.open(FX / "heic" / e.rel)).convert("L")
        got = Image.open(tmp_path / "thumbs" / e.content_hash[:2] / f"{e.content_hash}.t256.webp").convert("L")
        assert got.size == ref.size  # small images are not upscaled; orientation applied
        assert ssim(np.asarray(got), np.asarray(ref)) >= 0.98
    portrait = next(e for e in res.entries if e.rel == "IMG_0002.HEIC")
    assert (portrait.extra["width"], portrait.extra["height"]) == (64, 96)


@pytest.mark.integration
@pytest.mark.tc("SCAN-009")
def test_scan_009_raw_embedded_preview(sb, sandbox_tree, tmp_path):
    for f in sorted((FX / "raw").iterdir()):
        put(sandbox_tree, f.name, f)
    res = scan(sb, "photos", "", INC, [], tz=TW, thumbs_dir=tmp_path / "t")
    assert sorted(e.rel for e in res.entries) == ["sample.arw", "sample.cr3", "sample.dng", "sample.nef", "sample.raf"]
    assert [(s.rel, s.reason) for s in res.skipped] == [("no_preview.dng", "DECODE_RAW_MISSING")]
    for e in res.entries:
        assert (e.extra["width"], e.extra["height"]) == (160, 120)
        assert (tmp_path / "t" / e.content_hash[:2] / f"{e.content_hash}.t256.webp").exists()
        assert e.date_source == "exif_offset"


CORNER = {1: "TL", 2: "TR", 3: "BR", 4: "BL", 5: "TL", 6: "TR", 7: "BR", 8: "BL"}


@pytest.mark.integration
@pytest.mark.tc("SCAN-011")
@pytest.mark.parametrize("o", range(1, 9))
def test_scan_011_orientation_thumbnails(sb, sandbox_tree, tmp_path, o):
    put(sandbox_tree, f"orient_{o}.jpg", FX / f"orientation/orient_{o}.jpg")
    [e] = scan(sb, "photos", "", INC, [], tz=TW, thumbs_dir=tmp_path / "t").entries
    im = np.asarray(Image.open(tmp_path / "t" / e.content_hash[:2] / f"{e.content_hash}.t256.webp").convert("RGB"))
    h, w, _ = im.shape
    assert (w, h) == ((80, 40) if o <= 4 else (40, 80))
    corners = {"TL": im[2, 2], "TR": im[2, w - 3], "BL": im[h - 3, 2], "BR": im[h - 3, w - 3]}
    red = [k for k, px in corners.items() if px[0] > 180 and px[1] < 90]
    assert red == [CORNER[o]]


@pytest.mark.integration
@pytest.mark.tc("SCAN-015")
def test_scan_015_permission_denied_folder_skipped(sb, sandbox_tree, monkeypatch):
    put(sandbox_tree, "ok/a.jpg", FX / "basic/IMG_0001.jpg")
    put(sandbox_tree, "locked/b.jpg", FX / "basic/IMG_0002.jpg")
    put(sandbox_tree, "tcc/c.jpg", FX / "basic/IMG_0003.jpg")
    real = os.scandir

    def fake(path):
        name = Path(path).name
        if name == "locked":
            raise PermissionError(errno.EACCES, "denied")
        if name == "tcc":
            raise PermissionError(errno.EPERM, "operation not permitted")
        return real(path)

    monkeypatch.setattr(scanner_mod.os, "scandir", fake)
    res = scan(sb, "photos", "", INC, [], tz=TW)
    assert [e.rel for e in res.entries] == ["ok/a.jpg"]
    assert [(s.rel, s.reason, s.fix) for s in res.skipped] == [
        ("locked", "FS_PERMISSION_DENIED", None),
        ("tcc", "FS_PERMISSION_DENIED", "fs.roots"),
    ]


@pytest.mark.integration
@pytest.mark.tc("SCAN-016")
def test_scan_016_cloud_placeholder_not_opened(sb, sandbox_tree, monkeypatch):
    put(sandbox_tree, "local.jpg", FX / "basic/IMG_0001.jpg")
    put(sandbox_tree, "cloud.jpg", FX / "basic/IMG_0002.jpg")
    monkeypatch.setattr(scanner_mod, "_is_placeholder", lambda st, path: path.endswith("cloud.jpg"))
    opened = []
    real_open = scanner_mod.Image.open
    monkeypatch.setattr(scanner_mod.Image, "open", lambda p, *a: opened.append(str(p)) or real_open(p, *a))
    res = scan(sb, "photos", "", INC, [], tz=TW)
    assert [e.rel for e in res.entries] == ["local.jpg"]
    assert [(s.rel, s.reason) for s in res.skipped] == [("cloud.jpg", "cloud_placeholder")]
    assert not any(p.endswith("cloud.jpg") for p in opened)


@pytest.mark.integration
@pytest.mark.os_windows
@pytest.mark.tc("SCAN-016")
def test_scan_016_windows_offline_attribute(sb, sandbox_tree):
    import ctypes

    p = put(sandbox_tree, "cloud.jpg", FX / "basic/IMG_0002.jpg")
    assert ctypes.windll.kernel32.SetFileAttributesW(str(p), 0x1000)  # FILE_ATTRIBUTE_OFFLINE
    res = scan(sb, "photos", "", INC, [], tz=TW)
    assert [(s.rel, s.reason) for s in res.skipped] == [("cloud.jpg", "cloud_placeholder")]


@pytest.mark.unit
@pytest.mark.tc("SCAN-021")
def test_scan_021_thumbhash_and_perceptual_hashes():
    im = Image.open(FX / "basic/IMG_0007.jpg").convert("RGB").resize((256, 192))
    th = thumbhash_encode(im)
    assert 5 <= len(th) <= 25
    half = im.resize((128, 96), Image.Resampling.LANCZOS)
    assert bin(int(phash(im), 16) ^ int(phash(half), 16)).count("1") <= 6
    assert len(phash(im)) == 16 and len(dhash(im)) == 16
    other = Image.open(FX / "basic/IMG_0013.jpg").convert("RGB").resize((256, 192))
    assert phash(other) != phash(im)


@pytest.mark.integration
@pytest.mark.tc("SCAN-023")
def test_scan_023_unicode_and_long_names(sb, sandbox_tree):
    names = [
        unicodedata.normalize("NFC", "Café.jpg"),
        unicodedata.normalize("NFD", "Crème.jpg"),
        "貓" * 81 + ".jpg",
        "🐱家族👨‍👩‍👧.jpg",
    ]
    assert len(names[2].encode()) == 247
    for n in names:
        put(sandbox_tree, n, FX / "basic/IMG_0001.jpg")
    res = scan(sb, "photos", "", INC, [], tz=TW)
    rels = sorted(e.rel for e in res.entries)
    assert rels == sorted(unicodedata.normalize("NFC", n) for n in names)
    assert all(unicodedata.is_normalized("NFC", r) for r in rels)


@pytest.mark.unit
@pytest.mark.tc("SCAN-025")
def test_scan_025_progress_indeterminate_then_determinate(sb, sandbox_tree):
    for i in range(12):
        put(sandbox_tree, f"{i}.jpg", FX / f"basic/IMG_{i + 1:04d}.jpg")

    class Rec:
        def __init__(self):
            self.ev = []

        def start(self, op_type, total=None, unit="items"):
            self.ev.append(("start", total))

        def phase(self, name):
            self.ev.append(("phase", name))

        def progress(self, done, total=None):
            self.ev.append(("progress", done, total))

        def finish(self, status):
            self.ev.append(("finish", status))

    r = Rec()
    scan(sb, "photos", "", INC, [], tz=TW, reporter=r)
    assert r.ev[0] == ("start", None)
    assert ("phase", "enumerate") in r.ev and ("phase", "hash") in r.ev
    i = r.ev.index(("phase", "hash"))
    assert all(e[2] is None for e in r.ev[:i] if e[0] == "progress")  # indeterminate: "found N"
    assert [e for e in r.ev[i:] if e[0] == "progress"][-1] == ("progress", 12, 12)
    assert r.ev[-1] == ("finish", "succeeded")
    r2 = Rec()
    scan(sb, "photos", "", INC, [], tz=TW, reporter=r2, previous_total=12)
    assert r2.ev[0] == ("start", 12)  # a previous total gives a rough estimate immediately
