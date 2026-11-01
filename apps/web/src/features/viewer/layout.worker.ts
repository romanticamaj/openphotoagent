// Layout Web Worker (10 §2.2): justified rows are computed off the main thread and sent back as a Float32Array
// [y, h, firstIndex, count, x0, w0, x1, w1, …] per row, so a 10 000-photo month never blocks scrolling.
import { justify } from "./layout";

export type LayoutRequest = { id: number; aspects: Float32Array; width: number; target: number };

self.onmessage = (e: MessageEvent<LayoutRequest>) => {
  const { id, aspects, width, target } = e.data;
  const l = justify(aspects, width, target);
  const out = new Float32Array(l.rows.reduce((n, r) => n + 4 + 2 * r.count, 0));
  let k = 0;
  for (const r of l.rows) {
    out[k++] = r.y;
    out[k++] = r.height;
    out[k++] = r.tiles[0]?.index ?? 0;
    out[k++] = r.count;
    for (const t of r.tiles) {
      out[k++] = t.x;
      out[k++] = t.w;
    }
  }
  (self as unknown as Worker).postMessage({ id, rows: out, height: l.height }, [out.buffer]);
};
