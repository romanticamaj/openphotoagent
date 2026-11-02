"""`sidecar_xmp` action (09b XMP-001/003/004/005): XMP written next to the photo, merged with an existing one
(backed up first), the photo itself never touched, undo restores."""

import xml.etree.ElementTree as ET

import pytest
from opagent_fs.executor import Action

RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
PAYLOAD = {"subject": ["貓"], "description": "一隻貓", "hierarchical": ["OpenPhotoAgent|寵物|貓"]}


def _photo(env, rel, data):
    p = env.tree.sorted / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _xmp_action(rel, xmp_rel):
    return Action(f"x-{rel}", "sidecar_xmp", "sorted", rel, "sorted", xmp_rel, "", payload=PAYLOAD)


@pytest.mark.integration
@pytest.mark.tc("XMP-001", "XMP-004")
def test_xmp_001_004_written_next_to_the_photo_which_is_untouched(env, helpers):
    p = _photo(env, "a/IMG_1.jpg", b"jpeg bytes")
    before = (helpers.b3(p), p.stat().st_mtime_ns)
    rep = env.ex.apply("px", [_xmp_action("a/IMG_1.jpg", "a/IMG_1.xmp")])
    assert [r.status for r in rep.results] == ["done"]
    x = env.tree.sorted / "a" / "IMG_1.xmp"
    root = ET.fromstring(x.read_bytes())
    assert [li.text for li in root.iter(f"{RDF}li")][:1] == ["貓"]
    assert (helpers.b3(p), p.stat().st_mtime_ns) == before


@pytest.mark.integration
@pytest.mark.tc("XMP-003", "XMP-005")
def test_xmp_003_005_existing_xmp_is_merged_backed_up_and_restored_by_undo(env, helpers):
    _photo(env, "b/IMG_2.jpg", b"jpeg")
    old = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="5"/>'
        "</rdf:RDF></x:xmpmeta>"
    ).encode()
    x = _photo(env, "b/IMG_2.xmp", old)
    env.ex.apply("py", [_xmp_action("b/IMG_2.jpg", "b/IMG_2.xmp")])
    merged = x.read_bytes()
    assert b'Rating="5"' in merged and "貓".encode() in merged
    assert list((env.tree.sorted / ".opagent-trash" / "py").rglob("IMG_2.xmp"))  # backup before writing
    assert [r.status for r in env.ex.undo("py")] == ["undone"]
    assert x.read_bytes() == old
    _photo(env, "c/IMG_3.jpg", b"jpeg")
    env.ex.apply("pz", [_xmp_action("c/IMG_3.jpg", "c/IMG_3.xmp")])
    env.ex.undo("pz")
    assert not (env.tree.sorted / "c" / "IMG_3.xmp").exists()  # created by us: undo deletes it
