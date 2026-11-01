import { describe, expect, it } from "vitest";
import { ElapsedClock, OperationStore } from "./operations";

describe("operations store", () => {
  it("[OP-009] drops out-of-order older events by seq", () => {
    const s = new OperationStore();
    expect(s.apply({ id: "op1", seq: 812, fraction: 0.5 })).toBe(true);
    expect(s.apply({ id: "op1", seq: 810, fraction: 0.4 })).toBe(false);
    expect(s.get("op1")?.fraction).toBe(0.5);
    expect(s.apply({ id: "op1", seq: 813, fraction: 0.6 })).toBe(true);
  });

  it("[OP-014] server clock offset keeps elapsed monotonic", () => {
    const c = new ElapsedClock();
    // server is 4 s ahead of the local clock
    c.sync({ serverTime: 104_000, localNow: 100_000, activeMs: 10_000, updatedAt: 103_000, running: true });
    const a = c.elapsedMs(100_500);
    const b = c.elapsedMs(101_500);
    expect(a).toBe(10_000 + (100_500 + 4_000 - 103_000));
    expect(b - a).toBe(1_000);
    // a later sync that would move backwards never shows a smaller number
    c.sync({ serverTime: 105_000, localNow: 101_600, activeMs: 10_100, updatedAt: 105_000, running: true });
    expect(c.elapsedMs(101_700)).toBeGreaterThanOrEqual(b);
  });
});
