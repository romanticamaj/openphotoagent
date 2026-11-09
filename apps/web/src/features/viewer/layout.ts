// Justified photo layout (10 §2.2): rows keep aspect ratios and fill the width; each row's height stays within
// [0.8, 1.25] × target; the last row is never stretched. Pure, so it runs unchanged inside the layout Web Worker.

export type Tile = { index: number; x: number; w: number };
export type Row = { y: number; height: number; count: number; tiles: Tile[] };
export type Layout = { rows: Row[]; height: number; width: number };

export function justify(aspects: ArrayLike<number>, width: number, target: number, gap = 2): Layout {
  const rows: Row[] = [];
  let y = 0;
  let start = 0;
  const n = aspects.length;
  const emit = (from: number, to: number, height: number, stretch: boolean) => {
    const tiles: Tile[] = [];
    let x = 0;
    const count = to - from;
    const sum = sumAspect(aspects, from, to);
    const avail = width - gap * (count - 1);
    for (let i = from; i < to; i++) {
      let w = aspects[i] * height;
      if (stretch) w = (aspects[i] / sum) * avail;
      tiles.push({ index: i, x, w });
      x += w + gap;
    }
    rows.push({ y, height, count, tiles });
    y += height + gap;
  };
  while (start < n) {
    let end = start;
    let h = Infinity;
    let prevH = Infinity;
    while (end < n) {
      prevH = h;
      end++;
      h = (width - gap * (end - start - 1)) / sumAspect(aspects, start, end);
      if (h <= target) break;
    }
    if (h > target) {
      emit(start, end, Math.min(target, h), false); // last row: natural size, not stretched
      break;
    }
    // choose between ending before or after the item that crossed the target: whichever height is closer
    const withItem = h;
    const without = prevH;
    const canDrop = end - start > 1 && without <= target * 1.25;
    if (canDrop && Math.abs(without - target) < Math.abs(withItem - target)) {
      end--;
      h = without;
    }
    if (h < target * 0.8 && end - start === 1) {
      emit(start, end, target * 0.8, true); // a single very wide panorama: cropped to the row
    } else {
      emit(start, end, Math.max(h, target * 0.8), true);
    }
    start = end;
  }
  return { rows, height: Math.max(0, y - gap), width };
}

function sumAspect(a: ArrayLike<number>, from: number, to: number): number {
  let s = 0;
  for (let i = from; i < to; i++) s += a[i];
  return s;
}

/** Tiles of the rows intersecting [top, top + viewport] plus `overscan` rows on each side (10 §5.1 DOM budget). */
export function visibleTiles(layout: Layout, top: number, viewport: number, overscan = 2): Tile[] {
  const rows = layout.rows;
  let lo = 0;
  let hi = rows.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (rows[mid].y + rows[mid].height < top) lo = mid + 1;
    else hi = mid;
  }
  const first = Math.max(0, lo - overscan);
  let last = lo;
  while (last < rows.length - 1 && rows[last + 1].y < top + viewport) last++;
  last = Math.min(rows.length - 1, last + overscan);
  const out: Tile[] = [];
  for (let r = first; r <= last; r++) out.push(...rows[r].tiles);
  return out;
}

/** The smallest thumbnail whose long side covers the tile on this screen, at most 512 so decoded images stay in
 * the memory budget (VIEW-022, VIEW-010). */
export function thumbVariant(w: number, h: number, dpr: number): "t256" | "t512" {
  return Math.max(w, h) * dpr <= 256 ? "t256" : "t512";
}
