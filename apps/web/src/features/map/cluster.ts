// Grid clustering for the map (10 §2.5; MAP-003). Pure and deterministic: points fall into Web-Mercator grid cells
// sized for the zoom level (about 60 px on screen), so every photo belongs to exactly one cluster and totals add up.

export type GeoPoint = { id: string; lat: number; lon: number };
export type Cluster = { key: string; ids: string[]; count: number; lat: number; lon: number; bbox: [number, number, number, number] };

const CELL_PX = 60;
const TILE_PX = 256;

function mercator(lat: number, lon: number): [number, number] {
  const x = (lon + 180) / 360;
  const s = Math.sin((Math.max(-85.05, Math.min(85.05, lat)) * Math.PI) / 180);
  const y = 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
  return [x, y];
}

export function clusterPoints(points: GeoPoint[], zoom: number): Cluster[] {
  const cells = (TILE_PX * 2 ** Math.max(0, Math.floor(zoom))) / CELL_PX;
  const groups = new Map<string, GeoPoint[]>();
  for (const p of points) {
    const [x, y] = mercator(p.lat, p.lon);
    const key = `${Math.min(cells - 1, Math.floor(x * cells))}:${Math.min(cells - 1, Math.floor(y * cells))}`;
    const g = groups.get(key);
    if (g) g.push(p);
    else groups.set(key, [p]);
  }
  const out: Cluster[] = [];
  for (const [key, g] of groups) {
    const lat = g.reduce((s, p) => s + p.lat, 0) / g.length;
    const lon = g.reduce((s, p) => s + p.lon, 0) / g.length;
    const bbox: [number, number, number, number] = [
      Math.min(...g.map((p) => p.lon)),
      Math.min(...g.map((p) => p.lat)),
      Math.max(...g.map((p) => p.lon)),
      Math.max(...g.map((p) => p.lat)),
    ];
    out.push({ key, ids: g.map((p) => p.id).sort(), count: g.length, lat, lon, bbox });
  }
  return out.sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
}
