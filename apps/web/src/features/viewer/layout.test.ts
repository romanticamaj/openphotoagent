import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { justify, justifyDays, thumbVariant, visibleTiles } from "./layout";

const golden = JSON.parse(readFileSync(join(import.meta.dirname, "../../../../../fixtures/golden/justified/cases.json"), "utf8"));

describe("justified layout", () => {
  it("[VIEW-001] matches the golden row breaks at 1280 and 375 px", () => {
    for (const c of golden.cases) {
      const out = justify(c.aspects, c.width, c.target, c.gap);
      expect(out.rows.map((r) => r.count)).toEqual(c.rowCounts);
      out.rows.forEach((r, i) => {
        const last = i === out.rows.length - 1;
        if (!last) {
          expect(r.height).toBeGreaterThanOrEqual(c.target * 0.8 - 1e-6);
          expect(r.height).toBeLessThanOrEqual(c.target * 1.25 + 1e-6);
          const w = r.tiles.reduce((s, t) => s + t.w, 0) + c.gap * (r.count - 1);
          expect(Math.abs(w - c.width)).toBeLessThan(1); // fills the row exactly
        } else {
          expect(r.height).toBeLessThanOrEqual(c.target * 1.25 + 1e-6); // the last row is never stretched
        }
      });
    }
  });

  it("[VIEW-004] at most 400 tile elements on a 4K viewport in compact density", () => {
    // a 4K monitor at its default 150 % scaling is a 2560×1440 CSS-px viewport (10 §5.1 DOM budget)
    const aspects = Array.from({ length: 100_000 }, (_, i) => [1.5, 0.75, 1, 1.78, 0.66][i % 5]);
    const out = justify(aspects, 2560, 120, 2);
    for (const top of [0, 50_000, out.height / 2, out.height - 1440]) {
      const tiles = visibleTiles(out, top, 1440, 2);
      expect(tiles.length).toBeGreaterThan(0);
      expect(tiles.length).toBeLessThanOrEqual(400);
    }
  });
});

describe("which thumbnail a tile loads", () => {
  it("[VIEW-022] 回歸：依 tile 的長邊與螢幕密度選尺寸，不讓 256 px 的縮圖被放大到 480 px（驗證第 1 輪）", () => {
    expect(thumbVariant(180, 200, 1)).toBe("t256");
    expect(thumbVariant(480, 240, 1)).toBe("t512"); // a wide tile in a short row
    expect(thumbVariant(200, 200, 2)).toBe("t512"); // HiDPI
    expect(thumbVariant(600, 300, 2)).toBe("t512"); // the grid stops at 512: decoded images stay in budget (VIEW-010)
  });
});

describe("days inside a month", () => {
  it("[VIEW-023] 回歸：一個月依日期分段，每天有自己的標題（驗證第 1 輪：424 張擠成一塊）", () => {
    const aspects = [1.5, 1.5, 1.5, 0.75, 1.5, 1.5];
    const l = justifyDays(aspects, [2, 4], 1000, 200, 2, 28);
    expect(l.heads).toEqual([
      { y: 0, first: 0 },
      { y: expect.any(Number), first: 2 },
    ]);
    const firstOfDay2 = l.rows.find((r) => r.tiles[0].index === 2)!;
    const lastOfDay1 = l.rows.filter((r) => r.tiles[0].index < 2).at(-1)!;
    expect(l.heads![1].y).toBeGreaterThanOrEqual(lastOfDay1.y + lastOfDay1.height);
    expect(firstOfDay2.y).toBe(l.heads![1].y + 28); // the label sits right above its photos
    expect(l.rows.every((r) => r.tiles.every((t) => (t.index < 2) === (r.tiles[0].index < 2)))).toBe(true); // no row mixes days
    expect(l.height).toBe(Math.max(...l.rows.map((r) => r.y + r.height)));
  });
});
