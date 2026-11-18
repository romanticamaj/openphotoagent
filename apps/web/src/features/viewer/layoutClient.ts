// Main-thread side of the layout worker; falls back to synchronous layout where Workers are unavailable (tests).
import { justify, justifyDays, type Head, type Layout, type Row } from "./layout";

let worker: Worker | null = null;
let seq = 0;
const pending = new Map<number, (l: Layout) => void>();

function decode(rows: Float32Array, height: number, width: number, h?: Float32Array): Layout {
  const out: Row[] = [];
  let k = 0;
  while (k < rows.length) {
    const y = rows[k++];
    const h = rows[k++];
    const first = rows[k++];
    const count = rows[k++];
    const tiles = [];
    for (let i = 0; i < count; i++) tiles.push({ index: first + i, x: rows[k++], w: rows[k++] });
    out.push({ y, height: h, count, tiles });
  }
  const heads: Head[] = [];
  for (let i = 0; h && i + 1 < h.length; i += 2) heads.push({ y: h[i], first: h[i + 1] });
  return { rows: out, height, width, ...(h ? { heads } : {}) };
}

export function layoutAsync(aspects: number[], width: number, target: number, groups?: number[]): Promise<Layout> {
  if (typeof Worker === "undefined") return Promise.resolve(groups ? justifyDays(aspects, groups, width, target) : justify(aspects, width, target));
  if (worker === null) {
    worker = new Worker(new URL("./layout.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (e: MessageEvent<{ id: number; rows: Float32Array; height: number; width: number; heads?: Float32Array }>) => {
      const cb = pending.get(e.data.id);
      pending.delete(e.data.id);
      cb?.(decode(e.data.rows, e.data.height, 0, e.data.heads));
    };
  }
  const id = ++seq;
  const buf = new Float32Array(aspects);
  const g = groups ? new Int32Array(groups) : undefined;
  return new Promise((resolve) => {
    pending.set(id, (l) => resolve({ ...l, width }));
    worker!.postMessage({ id, aspects: buf, width, target, groups: g }, g ? [buf.buffer, g.buffer] : [buf.buffer]);
  });
}
