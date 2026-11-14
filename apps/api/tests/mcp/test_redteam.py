"""MCP red-team set, deterministic half: 110 classified cases (09b §9.2 SEC-REDTEAM-001; 05 §3.8; invariants 5, 6).

Payloads ride in every place a photo carries text — VLM caption, file name, folder name, EXIF free text, collection
name — and control cases try escalation, forged approvals, cross-folder reads and tampered handles. Pass means:
the payload reaches the client only inside UntrustedText (``origin`` set, neutralised, no control characters);
nothing is written without a person's approval; nothing outside the token's scope or folders is visible.
"""

import json
from pathlib import Path

import pytest
import yaml
from opagent_testing.mcp_harness import M

ROOT = Path(__file__).resolve().parents[4]
CASES = yaml.safe_load((ROOT / "fixtures/security/redteam.yaml").read_text(encoding="utf-8"))["cases"]
CONTROL_CHARS = "​‮‬\x1b\x00"


def fsname(s):
    return "".join("_" if c in "/\\\x00" else c for c in s)[:120]


def outside_untrusted(blob):
    """Every string that is not inside an object carrying ``origin``."""
    out = []

    def walk(v, inside):
        if isinstance(v, dict):
            here = inside or "origin" in v
            for x in v.values():
                walk(x, here)
        elif isinstance(v, list):
            for x in v:
                walk(x, inside)
        elif isinstance(v, str) and not inside:
            out.append(v)

    walk(blob, False)
    return out


def marker(payload):
    """A distinctive run of the payload that sanitising would leave in place, if it leaked."""
    words = [w for w in payload.replace("/", " ").split() if len(w) >= 6 and w.isascii() and w.isalpha()]
    return words[0] if words else payload[:8]


def search_all(m, pat):
    out, cursor = [], None
    for _ in range(20):
        args = {"query": "貓", "limit": 50, "parse_query": False, **({"cursor": cursor} if cursor else {})}
        page = m.ok(pat, "search_photos", args)
        out += page["results"]
        cursor = page.get("next_cursor")
        if not cursor:
            return out
    raise AssertionError("pagination did not end")


@pytest.fixture
def m(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path)
    yield x
    x.eng.dispose()


@pytest.mark.security
@pytest.mark.tc("SEC-REDTEAM-001")
def test_sec_redteam_001(m):
    assert len(CASES) >= 100 and len({c["id"] for c in CASES}) == len(CASES)
    assert all(c["category"] and c["expect"] for c in CASES)
    inj = [c for c in CASES if c["carrier"] != "control"]
    ids = {}
    for c in inj:
        p, cid = c["payload"], c["id"]
        if c["carrier"] == "caption":
            ids[cid] = m.add(f"rt/{cid}.jpg", caption=p + " 貓")
        elif c["carrier"] == "filename":
            ids[cid] = m.add(f"rt/{cid}-{fsname(p)}.jpg", caption="貓")
        elif c["carrier"] == "folder":
            ids[cid] = m.add(f"rt/{fsname(p)}/{cid}.jpg", caption="貓")
        elif c["carrier"] == "exif":
            ids[cid] = m.add(f"rt/{cid}-exif.jpg", caption="貓", exif={"ImageDescription": p, "UserComment": p})
    m.index()
    m.settings(exif_free_text=True)
    pat = m.token(scopes=["photos:read", "collections:write"])

    # collection names written by the agent come back as data, cleaned, never as templates
    created = 0
    for c in (c for c in inj if c["carrier"] == "collection"):
        out = m.call(pat, "create_collection", {"name": c["payload"]})
        if out["result"]["isError"]:  # refused by the input schema (e.g. too long) is a safe outcome too
            assert "INVALID_ARGUMENT" in json.dumps(out["result"]), (c["id"], out)
        else:
            created += 1
    cols = m.ok(pat, "list_collections", {})["collections"]
    assert len(cols) == created > 0
    for col in cols:
        assert col["name"]["origin"] and not any(ch in col["name"]["text"] for ch in CONTROL_CHARS + "{}")

    # every photo-borne payload: only inside UntrustedText
    by_id = {r["id"]: r for r in search_all(m, pat)}
    assert set(ids.values()) <= set(by_id)
    for c in inj:
        if c["carrier"] in ("collection",):
            continue
        r = by_id[ids[c["id"]]]
        if c["carrier"] == "exif":
            r = m.ok(pat, "get_photo", {"id": ids[c["id"]], "include": ["exif", "locations"], "thumbnail_size": 0})
        leaked = [s for s in outside_untrusted(r) if marker(c["payload"]) in s]
        assert leaked == [], (c["id"], c["carrier"], leaked)
        blob = json.dumps(r, ensure_ascii=False)
        assert not any(ch in blob for ch in CONTROL_CHARS), c["id"]
        assert "https://evil.example" not in "".join(outside_untrusted(r)), c["id"]

    # control cases
    ro = m.token(scopes=["photos:read"])
    for tool, args in (("update_labels", {"photo_ids": [ids["RT-001"]], "add": ["x"]}), ("propose_organize", {})):
        out = m.call(ro, tool, args)
        assert out.get("error", {}).get("code") == -32601, (tool, out)  # RT-101, RT-102: not even listed
    lw = m.token(scopes=["photos:read", "labels:write"])
    assert m.call(lw, "apply_organize", {"plan_id": "pl_1"}).get("error", {}).get("code") == -32601  # RT-103
    r = m.client.post("/api/v1/mcp/tokens", json={"name": "x", "scopes": ["organize:apply"]}, headers=m.ui)
    assert r.status_code == 422 and r.json()["code"] == "SCOPE_NEVER_ISSUED"  # RT-104
    ap = m.token(scopes=["photos:read", "organize:propose"])
    out = m.call(ap, "apply_organize", {"plan_id": "pl_1"})  # RT-105: a plan id from a caption is nothing
    assert out.get("error") or out["result"]["isError"]
    folder_tok = m.token(scopes=["photos:read"], folders=[{"source": "photos", "path": "rt/inside"}])
    inside = m.add("rt/inside/ok.jpg", caption="貓")
    m.index()
    out = m.call(folder_tok, "get_photo", {"id": ids["RT-001"], "thumbnail_size": 0})  # RT-107
    assert out["result"]["isError"] and "NOT_FOUND" in json.dumps(out["result"])
    assert [x["id"] for x in search_all(m, folder_tok)] == [inside]  # RT-108
    page = m.ok(pat, "search_photos", {"query": "貓", "limit": 5, "parse_query": False})
    cur = page["next_cursor"]
    bad = cur[:-2] + ("A" if cur[-2] != "A" else "B") + cur[-1]
    out = m.call(pat, "search_photos", {"query": "貓", "cursor": bad})  # RT-109
    assert out["result"]["isError"] and "CURSOR_INVALID" in json.dumps(out["result"])
    sel = m.ok(pat, "search_photos", {"query": "貓", "limit": 5, "parse_query": False, "return_selection": True})
    other = m.token(scopes=["photos:read", "collections:write"])
    cid = m.ok(other, "create_collection", {"name": "x"})["collection_id"]
    out = m.call(other, "add_to_collection", {"collection_id": cid, "selection_id": sel["selection_id"]})  # RT-110
    assert out["result"]["isError"] and "SELECTION_INVALID" in json.dumps(out["result"])

    # RT-106 and the whole session: nothing was written to disk-facing state without a person
    assert m.sql("select count(*) from output_action where status in ('done', 'undone')").scalar() == 0
    assert m.sql("select count(*) from approval where status = 'approved'").scalar() == 0
    assert m.sql("select count(*) from output_plan where status in ('applied', 'applying')").scalar() == 0
