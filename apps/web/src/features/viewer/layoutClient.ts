// Main-thread side of the layout worker; falls back to synchronous layout where Workers are unavailable (tests).
import { justify, type Layout, type Row } from "./layout";

let worker: Worker | null = null;
let seq = 0;
const pending = new Map<number, (l: Layout) => void>();

function decode(rows: Float32Array, height: number, width: number): Layout {
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
  return { rows: out, height, width };
}

export function layoutAsync(aspects: number[], width: number, target: number): Promise<Layout> {
  if (typeof Worker === "undefined") return Promise.resolve(justify(aspects, width, target));
  if (worker === null) {
    worker = new Worker(new URL("./layout.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (e: MessageEvent<{ id: number; rows: Float32Array; height: number; width: number }>) => {
      const cb = pending.get(e.data.id);
      pending.delete(e.data.id);
      cb?.(decode(e.data.rows, e.data.height, 0));
    };
  }
  const id = ++seq;
  const buf = new Float32Array(aspects);
  return new Promise((resolve) => {
    pending.set(id, (l) => resolve({ ...l, width }));
    worker!.postMessage({ id, aspects: buf, width, target }, [buf.buffer]);
  });
}
