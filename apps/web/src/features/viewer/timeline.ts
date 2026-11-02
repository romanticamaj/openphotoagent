// Timeline geometry (10 §2.2–2.3): month buckets stacked newest first; heights are estimated from the photo count
// until the bucket is laid out, so the scrubber can map any month to a y offset without loading thumbnails.

export type Bucket = { month: string; count: number };
export const HEADER_H = 44;
const AVG_ASPECT = 1.4;

export class TimelineModel {
  private heights: number[];
  private offsets: number[] = [];
  private cum: number[] = [];
  readonly totalCount: number;

  constructor(readonly buckets: Bucket[], readonly width: number, readonly target: number) {
    this.heights = buckets.map((b) => HEADER_H + estimate(b.count, width, target));
    let c = 0;
    for (const b of buckets) {
      this.cum.push(c);
      c += b.count;
    }
    this.totalCount = c;
    this.reflow();
  }

  private reflow() {
    let y = 0;
    this.offsets = this.heights.map((h) => {
      const o = y;
      y += h;
      return o;
    });
  }

  height(i: number): number {
    return this.heights[i];
  }
  offset(i: number): number {
    return this.offsets[i] ?? this.total();
  }
  total(): number {
    return this.heights.reduce((a, b) => a + b, 0);
  }
  setHeight(i: number, h: number) {
    if (Math.abs(this.heights[i] - h) < 0.5) return;
    this.heights[i] = h;
    this.reflow();
  }
  indexAt(y: number): number {
    return Math.max(0, upper(this.offsets, y) - 1);
  }
  offsetOfMonth(month: string): number {
    const i = this.buckets.findIndex((b) => b.month === month);
    return i < 0 ? 0 : this.offset(i);
  }
  /** Scrubber: each month takes space proportional to its photo count (10 §2.3). */
  scrubFraction(i: number): number {
    return this.totalCount ? this.cum[i] / this.totalCount : 0;
  }
  indexAtScrub(f: number): number {
    const n = Math.min(Math.max(f, 0), 1) * this.totalCount;
    return Math.min(this.buckets.length - 1, Math.max(0, upper(this.cum, n) - 1));
  }
}

function estimate(count: number, width: number, target: number): number {
  const perRow = Math.max(1, Math.floor(width / (target * AVG_ASPECT)));
  return Math.ceil(count / perRow) * (target + 2);
}

/** first index whose value is > x */
function upper(a: number[], x: number): number {
  let lo = 0;
  let hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (a[mid] <= x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export function monthLabel(month: string, lang: string): string | null {
  const m = /^(\d{4})-(\d{2})$/.exec(month);
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, 1));
  if (lang.startsWith("zh")) return `${Number(m[1])} 年 ${Number(m[2])} 月`;
  return new Intl.DateTimeFormat(lang, { month: "long", year: "numeric", timeZone: "UTC" }).format(d);
}
