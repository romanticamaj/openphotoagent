import { describe, expect, it } from "vitest";
import { unwrapRings } from "./geo";

describe("borders across the date line", () => {
  it("[MAP-013] 回歸：跨 180 度經線的國界不畫成橫跨海洋的直線（驗證第 3 輪）", () => {
    const fc = {
      type: "FeatureCollection" as const,
      features: [
        { type: "Feature" as const, id: "643", properties: {}, geometry: { type: "Polygon" as const, coordinates: [[[170, 60], [-170, 60], [-170, 65], [170, 65], [170, 60]]] } },
        { type: "Feature" as const, id: "010", properties: {}, geometry: { type: "Polygon" as const, coordinates: [[[-180, -80], [180, -80], [180, -90], [-180, -90], [-180, -80]]] } },
        { type: "Feature" as const, id: "158", properties: {}, geometry: { type: "Polygon" as const, coordinates: [[[120, 22], [122, 22], [122, 25], [120, 22]]] } },
      ],
    };
    const out = unwrapRings(fc);
    expect((out.features[0].geometry as GeoJSON.Polygon).coordinates[0]).toEqual([[170, 60], [190, 60], [190, 65], [170, 65], [170, 60]]);
    expect(out.features[1]).toEqual(fc.features[1]); // Antarctica circles the pole: left as it is
    expect(out.features[2]).toEqual(fc.features[2]); // Taiwan: nothing to do
    expect(out.features).toHaveLength(3); // no country is dropped (MAP-001 counts them)
  });
});
