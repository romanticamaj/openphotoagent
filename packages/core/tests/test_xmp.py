"""XMP sidecar content (09b XMP-*; 02 output_rule `sidecar_xmp`): pure build / merge, safe escaping."""

import xml.etree.ElementTree as ET

import pytest
from opagent_core.xmp import build_xmp

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "lr": "http://ns.adobe.com/lightroom/1.0/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
}
PAYLOAD = {"subject": ["貓", "海灘"], "description": "貓在海灘上", "hierarchical": ["OpenPhotoAgent|寵物|貓"]}


def _desc(xml: bytes):
    root = ET.fromstring(xml)
    return root.find("rdf:RDF/rdf:Description", NS)


def _bag(d, path):
    return [li.text for li in d.findall(f"{path}/rdf:Bag/rdf:li", NS)]


@pytest.mark.unit
@pytest.mark.tc("XMP-001")
def test_xmp_001_fields():
    d = _desc(build_xmp(PAYLOAD, None))
    assert _bag(d, "dc:subject") == ["貓", "海灘"]
    assert [li.text for li in d.findall("dc:description/rdf:Alt/rdf:li", NS)] == ["貓在海灘上"]
    assert _bag(d, "lr:hierarchicalSubject") == ["OpenPhotoAgent|寵物|貓"]


EXISTING = """<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:acme="http://example.com/acme/1.0/" xmp:Rating="4">
<dc:subject><rdf:Bag><rdf:li>旅行</rdf:li><rdf:li>貓</rdf:li></rdf:Bag></dc:subject>
<acme:Secret>keep me</acme:Secret>
</rdf:Description></rdf:RDF></x:xmpmeta>
<?xpacket end="w"?>""".encode()


@pytest.mark.unit
@pytest.mark.tc("XMP-003")
def test_xmp_003_merge_keeps_existing_fields_and_unknown_namespaces():
    out = build_xmp(PAYLOAD, EXISTING)
    d = _desc(out)
    assert d.get("{http://ns.adobe.com/xap/1.0/}Rating") == "4"
    assert d.find("{http://example.com/acme/1.0/}Secret").text == "keep me"
    assert _bag(d, "dc:subject") == ["旅行", "貓", "海灘"]  # union, existing order first, no duplicates
    assert b'xmlns:acme="http://example.com/acme/1.0/"' in out  # prefixes preserved for other tools


@pytest.mark.unit
@pytest.mark.tc("XMP-006")
def test_xmp_006_untrusted_text_is_escaped_and_valid():
    nasty = 'x ]]> <script>alert(1)</script> & "q" \x00\x07\x1b end'
    out = build_xmp({"subject": [nasty], "description": nasty, "hierarchical": []}, None)
    d = _desc(out)  # parses: valid XML
    assert b"<script>" not in out and b"]]>" not in out.replace(b"<?xpacket", b"")
    clean = _bag(d, "dc:subject")[0]
    assert "<script>alert(1)</script>" in clean and "]]>" in clean and "\x00" not in clean and "\x1b" not in clean


@pytest.mark.unit
@pytest.mark.tc("XMP-006")
def test_xmp_006_hostile_existing_xmp_is_not_expanded():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">'
        '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;">]>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="">&lol3;</rdf:Description></rdf:RDF></x:xmpmeta>'
    ).encode()
    out = build_xmp(PAYLOAD, bomb)
    assert b"lollol" not in out  # never expanded: the file is treated as unreadable (its backup keeps it)
    assert _bag(_desc(out), "dc:subject") == ["貓", "海灘"]
