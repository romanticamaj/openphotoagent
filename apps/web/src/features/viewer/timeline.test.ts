import { describe, expect, it } from "vitest";
import { TimelineModel, monthLabel } from "./timeline";

const B = [
  { month: "2025-07", count: 312 },
  { month: "2019-03", count: 88 },
  { month: "unknown", count: 4 },
];

describe("timeline model (10 §2.2–2.3)", () => {
  it("estimates unlaid buckets from count × average area / width and stacks them newest first", () => {
    const m = new TimelineModel(B, 1000, 200);
    expect(m.offset(0)).toBe(0);
    expect(m.offset(1)).toBeCloseTo(m.height(0));
    expect(m.total()).toBeCloseTo(m.height(0) + m.height(1) + m.height(2));
    expect(m.height(0)).toBeGreaterThan(m.height(1));
  });

  it("maps a y offset back to its bucket and a month to its offset (scrubber jump without loading)", () => {
    const m = new TimelineModel(B, 1000, 200);
    expect(m.indexAt(0)).toBe(0);
    expect(m.indexAt(m.offset(1) + 1)).toBe(1);
    expect(m.indexAt(m.total() + 50)).toBe(2);
    expect(m.offsetOfMonth("2019-03")).toBe(m.offset(1));
  });

  it("replaces an estimate with the real height after layout and shifts later buckets", () => {
    const m = new TimelineModel(B, 1000, 200);
    const before = m.offset(2);
    m.setHeight(0, m.height(0) + 100);
    expect(m.offset(2)).toBeCloseTo(before + 100);
  });

  it("scrubber positions are proportional to photo counts", () => {
    const m = new TimelineModel(B, 1000, 200);
    expect(m.scrubFraction(1)).toBeCloseTo(312 / 404);
    expect(m.indexAtScrub(0.5)).toBe(0);
    expect(m.indexAtScrub(0.8)).toBe(1);
    expect(m.indexAtScrub(1)).toBe(2);
  });

  it("labels months for aria-valuetext in both languages", () => {
    expect(monthLabel("2019-03", "zh-TW")).toBe("2019 年 3 月");
    expect(monthLabel("2019-03", "en")).toBe("March 2019");
    expect(monthLabel("unknown", "zh-TW")).toBe(null);
  });
});
