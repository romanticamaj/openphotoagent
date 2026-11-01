import json
import shutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from opagent_fs.safepath import RootConfig, Sandbox
from opagent_worker.scanner import content_hash, list_files, read_meta

ROOT = Path(__file__).resolve().parents[4]
TW = timezone(timedelta(hours=8))


@pytest.fixture
def sb(sandbox_tree):
    return Sandbox([RootConfig(**r) for r in sandbox_tree.roots])


@pytest.mark.unit
@pytest.mark.tc("SCAN-001")
def test_scan_001_include_exclude_globs(sb, sandbox_tree):
    for rel in ["a.jpg", "b.heic", "c.png", "tmp/d.jpg", "x/tmp/e.jpg", "deep/f.JPG", "deep/g.heic", "h.jpg.txt"]:
        p = sandbox_tree.photos / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    got = list_files(sb, "photos", "", include=["**/*.{jpg,heic}"], exclude=["**/tmp/**"])
    assert sorted(got) == ["a.jpg", "b.heic", "deep/f.JPG", "deep/g.heic"]


@pytest.mark.unit
@pytest.mark.tc("SCAN-003")
def test_scan_003_blake3_golden():
    golden = json.loads((ROOT / "fixtures/golden/scanner/SCAN-003.json").read_text())
    assert len(golden) == 5
    for name, h in golden.items():
        assert content_hash(ROOT / "fixtures/photos/basic" / name) == h


@pytest.mark.unit
@pytest.mark.tc("SCAN-013")
def test_scan_013_no_exif_uses_mtime(tmp_path):
    src = ROOT / "fixtures/photos/no-exif/NOEXIF_0001.jpg"
    p = tmp_path / "n.jpg"
    shutil.copyfile(src, p)
    mtime = datetime(2024, 3, 2, 1, 0, tzinfo=UTC)
    m = read_meta(p, mtime, TW)
    assert m.date_source == "mtime"
    assert m.taken_at == mtime
    # with EXIF the photo date comes from EXIF instead
    m2 = read_meta(ROOT / "fixtures/photos/basic/IMG_0001.jpg", mtime, TW)
    assert m2.date_source == "exif_offset"
    assert m2.taken_at == datetime(2023, 1, 15, 9, 30, tzinfo=TW)
