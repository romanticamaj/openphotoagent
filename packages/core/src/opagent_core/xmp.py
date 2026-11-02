"""XMP sidecar content (02 output_rule `sidecar_xmp`; 09b XMP-*). Pure: bytes in, bytes out.

We write ``dc:subject`` (keywords), ``dc:description`` (caption) and ``lr:hierarchicalSubject`` (category path),
the fields digiKam and Lightroom read. Merging into an existing XMP keeps every field and namespace we do not own
(ratings, labels, other tools' data); keyword lists are unions with the existing order first. Text from photos is
untrusted: it is escaped by the XML serializer and characters XML 1.0 forbids are dropped. An existing XMP is
parsed with defusedxml (no entity expansion, no external entities).
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from typing import Any

import defusedxml.ElementTree as SafeET

X = "adobe:ns:meta/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
DC = "http://purl.org/dc/elements/1.1/"
LR = "http://ns.adobe.com/lightroom/1.0/"
XML_NS = "http://www.w3.org/XML/1998/namespace"
OURS = {"x": X, "rdf": RDF, "dc": DC, "lr": LR}
PACKET_BEGIN = '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
PACKET_END = '\n<?xpacket end="w"?>\n'
_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _clean(s: Any) -> str:
    return _INVALID.sub("", str(s))


def _namespaces(existing: bytes) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for _event, (prefix, uri) in SafeET.iterparse(io.BytesIO(existing), events=["start-ns"]):
        out.append((prefix, uri))
    return out


def _bag_values(desc: ET.Element, tag: str) -> list[str]:
    el = desc.find(tag)
    if el is None:
        return []
    return [li.text or "" for li in el.iter(f"{{{RDF}}}li")]


def _set_bag(desc: ET.Element, tag: str, values: list[str]) -> None:
    old = desc.find(tag)
    if old is not None:
        desc.remove(old)
    if not values:
        return
    el = ET.SubElement(desc, tag)
    bag = ET.SubElement(el, f"{{{RDF}}}Bag")
    for v in values:
        ET.SubElement(bag, f"{{{RDF}}}li").text = v


def _set_alt(desc: ET.Element, tag: str, value: str) -> None:
    old = desc.find(tag)
    if old is not None:
        desc.remove(old)
    el = ET.SubElement(desc, tag)
    alt = ET.SubElement(el, f"{{{RDF}}}Alt")
    li = ET.SubElement(alt, f"{{{RDF}}}li")
    li.set(f"{{{XML_NS}}}lang", "x-default")
    li.text = value


def _union(existing: list[str], ours: list[str]) -> list[str]:
    out = list(existing)
    for v in ours:
        if v not in out:
            out.append(v)
    return out


def build_xmp(payload: dict[str, Any], existing: bytes | None) -> bytes:
    """Our fields from ``payload`` (``subject``, ``description``, ``hierarchical``), merged into ``existing``."""
    for prefix, uri in OURS.items():
        ET.register_namespace(prefix, uri)
    root: ET.Element | None = None
    if existing:
        try:
            for prefix, uri in _namespaces(existing):
                if prefix and prefix != "xml":
                    ET.register_namespace(prefix, uri)
            root = SafeET.fromstring(existing)
        except (ET.ParseError, ValueError):  # incl. defusedxml's refusals (entities, DTDs)
            root = None  # unreadable: the backup keeps the original, we write a clean packet
    if root is None or root.tag != f"{{{X}}}xmpmeta":
        root = ET.Element(f"{{{X}}}xmpmeta")
    rdf = root.find(f"{{{RDF}}}RDF")
    if rdf is None:
        rdf = ET.SubElement(root, f"{{{RDF}}}RDF")
    desc = rdf.find(f"{{{RDF}}}Description")
    if desc is None:
        desc = ET.SubElement(rdf, f"{{{RDF}}}Description")
        desc.set(f"{{{RDF}}}about", "")
    subject = [_clean(v) for v in payload.get("subject") or []]
    hierarchical = [_clean(v) for v in payload.get("hierarchical") or []]
    _set_bag(desc, f"{{{DC}}}subject", _union(_bag_values(desc, f"{{{DC}}}subject"), subject))
    if payload.get("description"):
        _set_alt(desc, f"{{{DC}}}description", _clean(payload["description"]))
    _set_bag(
        desc, f"{{{LR}}}hierarchicalSubject", _union(_bag_values(desc, f"{{{LR}}}hierarchicalSubject"), hierarchical)
    )
    ET.indent(root, space=" ")
    body = ET.tostring(root, encoding="unicode")
    return (PACKET_BEGIN + body + PACKET_END).encode("utf-8")
