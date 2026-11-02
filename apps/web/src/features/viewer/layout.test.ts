import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { justify, visibleTiles } from "./layout";

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
