"""What a folder holds, before anything is imported (ADR-0029): the worker is the only one that can look."""

import base64
import shutil
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from opagent_fs.safepath import RootConfig, Sandbox
from opagent_worker.folder_preview import folder_preview, folder_thumb
from PIL import Image

FX = Path(__file__).resolve().parents[3] / "fixtures/photos/basic"


def _sb(tree):
    return Sandbox([RootConfig(**r) for r in tree.roots])


def _tree(photos: Path) -> None:
    for i, sub in enumerate(("a", "a", "b/deep", "")):
        d = photos / sub
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FX / f"IMG_{i + 1:04d}.jpg", d / f"p{i}.jpg")
    (photos / "clip.mov").write_bytes(b"x" * 10)
    (photos / "notes.csv").write_bytes(b"y" * 10)
    (photos / ".hidden").mkdir()
    shutil.copyfile(FX / "IMG_0001.jpg", photos / ".hidden" / "no.jpg")


@pytest.mark.unit
@pytest.mark.tc("IMP-001")
def test_imp_001_counts_what_the_folder_holds(sandbox_tree):
    _tree(sandbox_tree.photos)
    p = folder_preview(_sb(sandbox_tree), "photos", "", UTC)
    assert (p["photos"], p["videos"], p["others"]) == (4, 1, 1)
    assert (
        p["bytes"]
        == sum(f.stat().st_size for f in sandbox_tree.photos.rglob("p*.jpg") if ".hidden" not in f.parts) + 10
    )
    assert p["subfolders"] == [{"name": "a", "photos": 2, "videos": 0}, {"name": "b", "photos": 1, "videos": 0}]
    assert p["truncated"] is False


@pytest.mark.unit
@pytest.mark.tc("IMP-001")
def test_imp_001_a_sample_spreads_over_the_folder_and_dates_it(sandbox_tree):
    _tree(sandbox_tree.photos)
    p = folder_preview(_sb(sandbox_tree), "photos", "", UTC, samples=2)
    assert len(p["samples"]) == 2
    assert p["samples"][0] != p["samples"][1]
    assert all(s.endswith(".jpg") and not s.startswith(".hidden") for s in p["samples"])
    assert datetime.fromisoformat(p["taken_from"]) <= datetime.fromisoformat(p["taken_to"])


@pytest.mark.unit
@pytest.mark.tc("IMP-001")
def test_imp_001_a_huge_folder_stops_counting_and_says_so(sandbox_tree):
    _tree(sandbox_tree.photos)
    p = folder_preview(_sb(sandbox_tree), "photos", "", UTC, max_files=2)
    assert p["truncated"] is True
    assert p["photos"] + p["videos"] + p["others"] == 2


@pytest.mark.unit
@pytest.mark.tc("IMP-001")
def test_imp_001_a_path_outside_the_root_is_refused(sandbox_tree):
    with pytest.raises(Exception, match=r"(?i)path|escape|\.\."):
        folder_preview(_sb(sandbox_tree), "photos", "../sorted", UTC)


@pytest.mark.unit
@pytest.mark.tc("IMP-002")
def test_imp_002_a_sample_thumbnail_is_a_small_jpeg(sandbox_tree):
    _tree(sandbox_tree.photos)
    r = folder_thumb(_sb(sandbox_tree), "photos", "a/p0.jpg")
    im = Image.open(BytesIO(base64.b64decode(r["thumbnail_b64"])))
    assert im.format == "JPEG"
    assert max(im.size) <= 256


@pytest.mark.unit
@pytest.mark.tc("IMP-002")
def test_imp_002_only_photos_get_a_thumbnail(sandbox_tree):
    _tree(sandbox_tree.photos)
    with pytest.raises(Exception, match=r"(?i)path|escape|\.\."):
        folder_thumb(_sb(sandbox_tree), "photos", "../sorted/x.jpg")
    with pytest.raises(ValueError, match="not a photo"):
        folder_thumb(_sb(sandbox_tree), "photos", "notes.csv")
