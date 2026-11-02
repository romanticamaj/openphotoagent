"""Pure MCP policy pieces: signed handles (MCP-TOOL-001, MCP-FOLD-002), rate limits (MCP-RATE-*), argument
redaction (MCP-AUD-002), media policy (MCP-MEDIA-002/003) and the auto-approve range (MCP-APPR-007)."""

from datetime import UTC, datetime, timedelta

import pytest
from opagent_core.auto_approve import AutoApproveError, validate_auto_approve
from opagent_mcp.handles import HandleError, Signer
from opagent_mcp.media import effective_size
from opagent_mcp.ratelimit import LIMITS, Bucket
from opagent_mcp.redact import redact

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


@pytest.mark.unit
@pytest.mark.tc("MCP-TOOL-001")
def test_cursor_is_signed_and_expires_after_10_minutes():
    s = Signer(b"k" * 32)
    c = s.issue("cur", {"q": "貓", "offset": 20}, token_id="t1", scope_key="all", now=T0)
    assert s.open("cur", c, token_id="t1", scope_key="all", now=T0 + timedelta(minutes=9))["offset"] == 20
    with pytest.raises(HandleError, match="expired"):
        s.open("cur", c, token_id="t1", scope_key="all", now=T0 + timedelta(minutes=10, seconds=1))
    tampered = c[:-2] + ("A" if c[-2] != "A" else "B") + c[-1]
    with pytest.raises(HandleError, match="signature"):
        s.open("cur", tampered, token_id="t1", scope_key="all", now=T0)
    with pytest.raises(HandleError):
        s.open("sel", c, token_id="t1", scope_key="all", now=T0)  # a cursor is not a selection


@pytest.mark.security
@pytest.mark.tc("MCP-FOLD-002")
def test_fold_002_selection_bound_to_token_and_folder_scope():
    s = Signer(b"k" * 32)
    sel = s.issue("sel", {"filters": {}}, token_id="wide", scope_key="photos:", now=T0)
    with pytest.raises(HandleError, match="scope"):
        s.open("sel", sel, token_id="narrow", scope_key="photos:2025", now=T0)
    with pytest.raises(HandleError, match="scope"):
        s.open("sel", sel, token_id="wide", scope_key="photos:2025", now=T0)


@pytest.mark.unit
@pytest.mark.tc("MCP-RATE-001")
def test_rate_001_burst_30_plus_120_per_minute():
    b = Bucket.new("read", now=T0)
    results = [b.take(now=T0 + timedelta(milliseconds=i)) for i in range(151)]  # all inside one minute
    assert all(ok for ok, _ in results[:150])
    ok, retry = results[150]
    assert not ok and 0 < retry <= 60


@pytest.mark.unit
@pytest.mark.tc("MCP-RATE-004", "MCP-RATE-005")
def test_rate_004_005_propose_and_faces_limits():
    assert LIMITS["propose"].per_window == 10 and LIMITS["propose"].window_s == 3600
    p = Bucket.new("propose", now=T0)
    assert [p.take(now=T0 + timedelta(seconds=i))[0] for i in range(11)] == [True] * 10 + [False]
    f = Bucket.new("faces_find", now=T0)
    assert [f.take(now=T0 + timedelta(milliseconds=10 * i))[0] for i in range(61)][-1] is False
    restored = Bucket.from_state("propose", p.state())
    assert restored.take(now=T0 + timedelta(minutes=2))[0] is False
    assert restored.take(now=T0 + timedelta(minutes=70))[0] is True


@pytest.mark.unit
@pytest.mark.tc("MCP-AUD-002")
def test_aud_002_ids_counted_query_kept():
    args = {"query": "去年海邊", "photo_ids": [f"id{i}" for i in range(500)], "scope": {"selection_id": "x" * 900}}
    r, h = redact(args)
    assert r["query"] == "去年海邊" and r["photo_ids"] == {"count": 500}
    assert r["scope"]["selection_id"] == {"redacted": True}
    assert len(h) == 64 and redact(dict(reversed(list(args.items()))))[1] == h


@pytest.mark.unit
@pytest.mark.tc("MCP-MEDIA-002", "MCP-MEDIA-003")
def test_media_strictest_wins():
    assert effective_size(512, has_scope=True, policy="thumb_256", egress="full") == (256, None)
    assert effective_size(256, has_scope=True, policy="none", egress="full") == (0, "policy")
    assert effective_size(256, has_scope=False, policy="thumb_1024", egress="full") == (0, "scope_missing")
    assert effective_size(1024, has_scope=True, policy="thumb_1024", egress="thumbs") == (256, None)
    assert effective_size(512, has_scope=True, policy="thumb_512", egress="results-only") == (
        0,
        "privacy_results_only",
    )
    assert effective_size(0, has_scope=True, policy="thumb_512", egress="full") == (0, None)
    # face media: faces:media + face policy none (default) -> withheld by policy
    assert effective_size(256, has_scope=True, policy="none", egress="full") == (0, "policy")


@pytest.mark.unit
@pytest.mark.tc("MCP-APPR-007")
def test_appr_007_auto_approve_never_for_destructive_or_oversized():
    from opagent_core.auto_approve import eligible

    cfg = validate_auto_approve({"actions": ["copy"], "max_actions": 500})
    assert eligible(cfg, ["copy"] * 500) is True
    assert eligible(cfg, ["copy"] * 501) is False
    assert eligible(cfg, ["copy"] * 10 + ["move"]) is False
    for bad in ("move", "hardlink", "trash", "overwrite"):
        with pytest.raises(AutoApproveError):
            validate_auto_approve({"actions": ["copy", bad]})  # cannot even be configured (TRASH-007)
        assert eligible(cfg, [bad]) is False
    assert eligible(cfg, ["copy"], overwrites=1) is False  # an overwriting copy is never automatic
