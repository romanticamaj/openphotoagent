"""Unit tests for core helpers that have no dedicated 09a ID (coverage of supporting code)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from opagent_core.clock import SystemClock
from opagent_core.config_hash import ConfigError, canonical_json
from opagent_core.decide import Policy, Stage, VlmResult, decide, route_embedding, temperature_softmax
from opagent_core.ids import uuid7
from opagent_core.operation import NullReporter, Operation, OpStatus, project_job_status
from opagent_core.path_template import compile_template
from opagent_core.planner import PlanItem, plan, summarize
from opagent_core.rules import RuleError, compile_when
from opagent_testing.clock import FakeClock

TW = timezone(timedelta(hours=8))


def test_system_clock_contract():
    c = SystemClock()
    assert c.now().tzinfo is not None
    assert c.monotonic() <= c.monotonic()

    async def run():
        fired = asyncio.Event()
        c.call_later(0, fired.set)
        await c.sleep(0)
        await asyncio.wait_for(fired.wait(), 1)

    asyncio.run(run())


def test_uuid7_is_time_ordered_v7():
    clk = FakeClock()
    a = uuid7(clk)
    clk.advance(0.002)
    b = uuid7(clk)
    assert uuid.UUID(a).version == 7 and a < b


def test_planner_by_date_and_summary():
    items = [
        PlanItem(
            "a1",
            "trip/IMG_1.jpg",
            "h1",
            datetime(2025, 7, 1, tzinfo=TW),
            "exif_offset",
            datetime(2024, 1, 1, tzinfo=TW),
        ),
        PlanItem("a2", "IMG_2.JPG", "h2", None, None, datetime(2024, 3, 2, tzinfo=TW)),
    ]
    acts = plan(items, "by-date", "out")
    assert [a.dst_rel for a in acts] == ["out/2025/2025-07/IMG_1.jpg", "out/2024/2024-03/IMG_2.JPG"]
    assert [a.date_source for a in acts] == ["exif_offset", "mtime"]
    assert summarize(acts) == {"copy": 2, "folders": 2, "total": 2}
    assert plan(items[:1], "by-date")[0].dst_rel == "2025/2025-07/IMG_1.jpg"


def test_misc_branches():
    assert route_embedding(Stage({}), 3).route == "vlm"
    assert route_embedding(Stage({"a": 0.95}), 3).route == "accept"
    assert temperature_softmax([], 0.05) == []
    assert decide().decision == "other"
    assert decide(emb=Stage({"a": 0.3, "b": 0.2}), policy=Policy(min_confidence=0.1, review=0.5)).decision == "other"
    assert decide(emb=Stage({"a": 0.7}), vlm=VlmResult("a", 0.1), policy=Policy(min_confidence=0.0)).decision
    with pytest.raises(ConfigError):
        canonical_json({"x": object()})
    assert compile_template("{stem}{ext}.bak").parts[-1].literal == ".bak"
    with pytest.raises(RuleError):
        compile_when("category == b'x'")
    with pytest.raises(RuleError):
        compile_when("1 is 1")
    f = compile_when("confidence > 'x' or category == 'food'")
    assert f({"category": "food", "confidence": 0.5}) is True
    assert compile_when("not category == 'a'")({"category": "b"}) is True


def test_null_reporter_and_failed_projection():
    r = NullReporter()
    r.start("scan", 10)
    r.phase("enumerate")
    r.progress(1, 10)
    r.finish("succeeded")
    op = Operation(id="j", type="job.label", status=OpStatus.running)
    project_job_status(op, "failed")
    assert op.status == OpStatus.failed
    assert datetime(2026, 1, 1, tzinfo=UTC).tzinfo is UTC
