import { describe, expect, it } from "vitest";
import { clusterPoints, type GeoPoint } from "./cluster";

function points(n: number, seed = 1): GeoPoint[] {
  let s = seed;
  const rnd = () => ((s = (s * 1103515245 + 12345) % 2 ** 31) / 2 ** 31);
  return Array.from({ length: n }, (_, i) => ({ id: `a${i}`, lat: rnd() * 170 - 85, lon: rnd() * 360 - 180 }));
}

describe("map clustering", () => {
  it("[MAP-003] every point belongs to exactly one cluster; totals are conserved; same input same output", () => {
    const pts = points(10_000);
    for (const zoom of [0, 2, 5, 9, 14]) {
      const cs = clusterPoints(pts, zoom);
      const ids = cs.flatMap((c) => c.ids);
      expect(ids.length).toBe(pts.length);
      expect(new Set(ids).size).toBe(pts.length);
      expect(cs.reduce((n, c) => n + c.count, 0)).toBe(pts.length);
      expect(clusterPoints(pts, zoom)).toEqual(cs);
      for (const c of cs) {
        expect(c.bbox[0]).toBeLessThanOrEqual(c.lon);
        expect(c.lon).toBeLessThanOrEqual(c.bbox[2]);
        expect(c.bbox[1]).toBeLessThanOrEqual(c.lat);
        expect(c.lat).toBeLessThanOrEqual(c.bbox[3]);
      }
    }
    expect(clusterPoints(pts, 0).length).toBeLessThan(clusterPoints(pts, 9).length); // zooming in splits clusters
  });

  it("[MAP-003] nearby photos group, far ones do not", () => {
    const pts: GeoPoint[] = [
      { id: "tokyo1", lat: 35.68, lon: 139.76 },
      { id: "tokyo2", lat: 35.69, lon: 139.7 },
      { id: "taipei", lat: 25.03, lon: 121.56 },
    ];
    const cs = clusterPoints(pts, 3);
    expect(cs.map((c) => c.ids.sort()).sort()).toEqual([["taipei"], ["tokyo1", "tokyo2"]]);
    expect(clusterPoints([], 3)).toEqual([]);
  });
});
