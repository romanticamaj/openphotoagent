// Natural Earth rings that cross the date line jump from 180 to -180, and the renderer draws that jump as a line
// across the whole map (MAP-013). Such rings are unwrapped to continue past 180. Antarctica runs along the pole from
// -180 to 180 on purpose and is left alone.
const POLE_WRAPPED = new Set(["010"]); // Antarctica (ISO 3166-1 numeric, as world-atlas ids)

function unwrap(ring: number[][]): number[][] {
  const lons = ring.map((p) => p[0]);
  if (Math.max(...lons) - Math.min(...lons) <= 180) return ring;
  return ring.map(([lon, lat, ...rest]) => [lon < 0 ? lon + 360 : lon, lat, ...rest]);
}

export function unwrapRings(fc: GeoJSON.FeatureCollection): GeoJSON.FeatureCollection {
  return {
    ...fc,
    features: fc.features.map((f) => {
      if (POLE_WRAPPED.has(String(f.id)) || !f.geometry) return f;
      const g = f.geometry;
      if (g.type === "Polygon") return { ...f, geometry: { ...g, coordinates: g.coordinates.map(unwrap) } };
      if (g.type === "MultiPolygon") return { ...f, geometry: { ...g, coordinates: g.coordinates.map((poly) => poly.map(unwrap)) } };
      return f;
    }),
  };
}

/** The box around the photos, for the first view (MAP-013): [[west, south], [east, north]]. */
export function boundsOf(points: Array<{ lat: number; lon: number }>): [[number, number], [number, number]] | null {
  if (!points.length) return null;
  const lats = points.map((p) => p.lat);
  const lons = points.map((p) => p.lon);
  return [
    [Math.min(...lons), Math.min(...lats)],
    [Math.max(...lons), Math.max(...lats)],
  ];
}
