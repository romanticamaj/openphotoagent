import os
import shutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from opagent_fs.safepath import RootConfig, Sandbox
from opagent_worker.scanner import HashCache, read_meta, scan

ROOT = Path(__file__).resolve().parents[4]
FX = ROOT / "fixtures/photos"
TW = timezone(timedelta(hours=8))
INC = ["**/*"]


@pytest.fixture
def sb(sandbox_tree):
    return Sandbox([RootConfig(**r) for r in sandbox_tree.roots])


def put(tree, rel, src=None, data=b"x"):
    p = tree.photos / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if src:
        shutil.copyfile(src, p)
    else:
        p.write_bytes(data)
    return p


@pytest.mark.unit
@pytest.mark.tc("SCAN-002")
def test_scan_002_always_excluded(sb, sandbox_tree):
    for rel in [
        ".DS_Store",
        "a/Thumbs.db",
        "desktop.ini",
        ".opagent-trash/p/x.jpg",
        "b/.opagent-tmp-123",
        "@eaDir/x.jpg",
        "c/@eaDir/SYNOFILE_THUMB_M.jpg",
        "keep.jpg",
    ]:
        put(sandbox_tree, rel, FX / "basic/IMG_0001.jpg")
    res = scan(sb, "photos", "", INC, [], tz=TW)
    assert [e.rel for e in res.entries] == ["keep.jpg"]


@pytest.mark.integration
@pytest.mark.tc("SCAN-004")
def test_scan_004_incremental_no_rereads(sb, sandbox_tree, tmp_path):
    for i in range(5):
        put(sandbox_tree, f"d/{i}.jpg", FX / f"basic/IMG_000{i + 1}.jpg")
    cache = HashCache(tmp_path / "cache.db")
    first = scan(sb, "photos", "", INC, [], tz=TW, cache=cache)
    assert first.bytes_read > 0
    second = scan(sb, "photos", "", INC, [], tz=TW, cache=cache)
    assert second.bytes_read == 0
    assert [e.content_hash for e in second.entries] == [e.content_hash for e in first.entries]


@pytest.mark.integration
@pytest.mark.tc("SCAN-005")
def test_scan_005_changed_file_rehashed_only(sb, sandbox_tree, tmp_path):
    paths = [put(sandbox_tree, f"{i}.jpg", FX / f"basic/IMG_000{i + 1}.jpg") for i in range(4)]
    cache = HashCache(tmp_path / "cache.db")
    before = {e.rel: e.content_hash for e in scan(sb, "photos", "", INC, [], tz=TW, cache=cache).entries}
    shutil.copyfile(FX / "basic/IMG_0009.jpg", paths[2])
    os.utime(paths[2], (paths[2].stat().st_atime, paths[2].stat().st_mtime + 10))
    res = scan(sb, "photos", "", INC, [], tz=TW, cache=cache)
    assert res.bytes_read == paths[2].stat().st_size
    after = {e.rel: e.content_hash for e in res.entries}
    assert [k for k in before if before[k] != after[k]] == ["2.jpg"]


@pytest.mark.unit
@pytest.mark.tc("SCAN-010")
def test_scan_010_corrupt_files(sb, sandbox_tree):
    for f in ("truncated.jpg", "empty.jpg", "text.jpg", "bad_exif.jpg"):
        put(sandbox_tree, f, FX / "corrupt" / f)
    res = scan(sb, "photos", "", INC, [], tz=TW)
    assert [e.rel for e in res.entries] == ["bad_exif.jpg"]
    assert sorted((s.rel, s.reason) for s in res.skipped) == [
        ("empty.jpg", "DECODE_FAILED"),
        ("text.jpg", "DECODE_FAILED"),
        ("truncated.jpg", "DECODE_FAILED"),
    ]


@pytest.mark.unit
@pytest.mark.tc("SCAN-012")
def test_scan_012_timezone_priority():
    mt = datetime(2020, 1, 1, tzinfo=UTC)
    a = read_meta(FX / "timezone/offset_plus9.jpg", mt, TW)
    assert (a.date_source, a.taken_at.utcoffset()) == ("exif_offset", timedelta(hours=9))
    b = read_meta(FX / "timezone/gps_tokyo.jpg", mt, TW)
    assert (b.date_source, b.taken_at.utcoffset()) == ("exif_gps_tz", timedelta(hours=9))
    c = read_meta(FX / "timezone/local_assumed.jpg", mt, TW)
    assert (c.date_source, c.taken_at.utcoffset()) == ("exif_local_assumed", timedelta(hours=8))
    assert b.taken_at.hour == 10 and c.taken_at.hour == 10


@pytest.mark.unit
@pytest.mark.tc("SCAN-014")
@pytest.mark.parametrize("name", ["garbage_zero.jpg", "garbage_month.jpg", "garbage_empty.jpg"])
def test_scan_014_garbage_dates_fall_back_to_mtime(name):
    mt = datetime(2021, 6, 1, tzinfo=UTC)
    m = read_meta(FX / "timezone" / name, mt, TW)
    assert (m.date_source, m.taken_at) == ("mtime", mt)


@pytest.mark.unit
@pytest.mark.tc("SCAN-017")
def test_scan_017_photos_library_package(sb, sandbox_tree):
    put(sandbox_tree, "Photos Library.photoslibrary/originals/a.jpg", FX / "basic/IMG_0001.jpg")
    put(sandbox_tree, "ok.jpg", FX / "basic/IMG_0002.jpg")
    res = scan(sb, "photos", "", INC, [], tz=TW)
    assert [e.rel for e in res.entries] == ["ok.jpg"]
    assert [(s.rel, s.reason) for s in res.skipped] == [("Photos Library.photoslibrary", "photos_library_package")]


@pytest.mark.unit
@pytest.mark.tc("SCAN-018")
def test_scan_018_symlinks_skipped_and_listed(sb, sandbox_tree):
    real = put(sandbox_tree, "real.jpg", FX / "basic/IMG_0001.jpg")
    os.symlink(real, sandbox_tree.photos / "link.jpg")
    os.symlink(sandbox_tree.outside, sandbox_tree.photos / "linkdir")
    res = scan(sb, "photos", "", INC, [], tz=TW)
    assert [e.rel for e in res.entries] == ["real.jpg"]
    assert sorted((s.rel, s.reason) for s in res.skipped) == [("link.jpg", "symlink"), ("linkdir", "symlink")]


@pytest.mark.unit
@pytest.mark.tc("SCAN-022")
def test_scan_022_videos_skipped_in_v0_1(sb, sandbox_tree):
    for f in ("clip.mp4", "clip.mov"):
        put(sandbox_tree, f, FX / "video" / f)
    res = scan(sb, "photos", "", INC, [], tz=TW)
    assert res.entries == []
    assert sorted((s.rel, s.reason) for s in res.skipped) == [
        ("clip.mov", "video_unsupported_v0_1"),
        ("clip.mp4", "video_unsupported_v0_1"),
    ]
