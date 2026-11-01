import asyncio
from datetime import UTC, datetime, timedelta

from opagent_testing.clock import FakeClock


def test_advance_fires_callbacks_in_due_order():
    clk = FakeClock()
    seen = []
    clk.call_later(5, lambda: seen.append(("b", clk.monotonic())))
    clk.call_later(1, lambda: seen.append(("a", clk.monotonic())))
    h = clk.call_later(3, lambda: seen.append(("x", 0)))
    h.cancel()
    clk.advance(10)
    assert seen == [("a", 1), ("b", 5)]
    assert clk.monotonic() == 10
    assert clk.now() == datetime(2026, 11, 1, tzinfo=UTC) + timedelta(seconds=10)


async def test_sleep_wakes_on_tick():
    clk = FakeClock()
    done = []

    async def worker():
        await clk.sleep(30)
        done.append(clk.monotonic())

    task = asyncio.create_task(worker())
    await clk.tick(0)
    await clk.tick(29)
    assert not done
    await clk.tick(1)
    await task
    assert done == [30]
