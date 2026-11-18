// Photo edits (ADR-0057): the maths shared by the grid and Lightbox (an SVG filter) and shares (the canvas), so a
// shared photo looks as it does in the app. Pure; the file is never written.

export type Edit = {
  v: 1;
  exposure?: number; // EV, -2..2
  contrast?: number; // -100..100
  highlights?: number;
  shadows?: number;
  saturation?: number;
  warmth?: number;
  straighten?: number; // degrees, -45..45
  crop?: { x: number; y: number; w: number; h: number }; // 0..1, after the quarter turn
  filter?: { name: FilterName; strength: number }; // 0..100
};

export const FILTERS = ["vivid", "warm", "cool", "fade", "mono", "noir"] as const;
export type FilterName = (typeof FILTERS)[number];

const ID = [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0];

/** No change at all: the photo is drawn as shot. */
export function isIdentity(e: Edit | null | undefined): boolean {
  if (!e) return true;
  const c = e.crop;
  const cropped = c && (c.x > 0 || c.y > 0 || c.w < 1 || c.h < 1);
  return !(e.exposure || e.contrast || e.highlights || e.shadows || e.saturation || e.warmth || e.straighten || cropped || (e.filter && e.filter.strength > 0));
}

/** The tone curve as 256 values: exposure, then contrast about the middle, then highlights and shadows. */
export function toneLut(e: Edit): number[] {
  const gain = 2 ** (e.exposure ?? 0);
  const k = 1 + (e.contrast ?? 0) / 100;
  const hi = (e.highlights ?? 0) / 100;
  const sh = (e.shadows ?? 0) / 100;
  const out: number[] = [];
  for (let i = 0; i < 256; i++) {
    let x = (i / 255) * gain;
    x = (x - 0.5) * k + 0.5;
    if (x > 0.5) x += hi * (x - 0.5) * (1.5 - x); // lifts or lowers the bright half, the white point held
    else x += sh * (0.5 - x) * (x + 0.5); // the dark half, the black point held
    out.push(Math.round(Math.min(1, Math.max(0, x)) * 255));
  }
  return out;
}

const LUMA = [0.2126, 0.7152, 0.0722];

function mul(a: number[], b: number[]): number[] {
  // 4×5 colour matrices (rows r, g, b, a; the 5th column an offset in 0..1), a applied after b
  const r: number[] = [];
  for (let row = 0; row < 4; row++) {
    for (let col = 0; col < 5; col++) {
      let v = col === 4 ? a[row * 5 + 4]! : 0;
      for (let k = 0; k < 4; k++) v += a[row * 5 + k]! * (col === 4 ? b[k * 5 + 4]! : b[k * 5 + col]!);
      r.push(v);
    }
  }
  return r;
}

function saturate(s: number): number[] {
  const [lr, lg, lb] = LUMA as [number, number, number];
  return [lr + (1 - lr) * s, lg - lg * s, lb - lb * s, 0, 0, lr - lr * s, lg + (1 - lg) * s, lb - lb * s, 0, 0, lr - lr * s, lg - lg * s, lb + (1 - lb) * s, 0, 0, 0, 0, 0, 1, 0];
}

function warm(t: number): number[] {
  // t in -1..1: red up and blue down for warm, the other way for cool
  return [1 + 0.12 * t, 0, 0, 0, 0, 0, 1 + 0.02 * t, 0, 0, 0, 0, 0, 1 - 0.12 * t, 0, 0, 0, 0, 0, 1, 0];
}

const PRESETS: Record<FilterName, number[]> = {
  vivid: saturate(1.35),
  warm: mul(warm(0.8), saturate(1.1)),
  cool: mul(warm(-0.8), saturate(0.95)),
  fade: [0.85, 0, 0, 0, 0.06, 0, 0.85, 0, 0, 0.06, 0, 0, 0.85, 0, 0.08, 0, 0, 0, 1, 0],
  mono: saturate(0),
  noir: mul([1.25, 0, 0, 0, -0.12, 0, 1.25, 0, 0, -0.12, 0, 0, 1.25, 0, -0.12, 0, 0, 0, 1, 0], saturate(0)),
};

/** Saturation, warmth and the filter as one 4×5 matrix, applied after the tone curve. */
export function colorMatrix(e: Edit): number[] {
  let m = ID.slice();
  if (e.saturation) m = mul(saturate(1 + e.saturation / 100), m);
  if (e.warmth) m = mul(warm(e.warmth / 100), m);
  if (e.filter && e.filter.strength > 0) {
    const p = PRESETS[e.filter.name];
    const s = e.filter.strength / 100;
    const mixed = p.map((v, i) => ID[i]! + (v - ID[i]!) * s);
    m = mul(mixed, m);
  }
  return m.map((v) => Math.round(v * 1e6) / 1e6 + 0);
}

/** How much a photo turned by `deg` must grow so its frame has no empty corner. */
export function coverScale(deg: number, w: number, h: number): number {
  if (!deg) return 1;
  const a = (Math.abs(deg) * Math.PI) / 180;
  return Math.max((w * Math.cos(a) + h * Math.sin(a)) / w, (w * Math.sin(a) + h * Math.cos(a)) / h);
}

/** The tone curve and colour matrix on RGBA pixels, in place (shares, ADR-0057). Alpha is left alone. */
export function applyEdit(px: Uint8ClampedArray, e: Edit): void {
  const lut = toneLut(e);
  const m = colorMatrix(e);
  for (let i = 0; i < px.length; i += 4) {
    const r = lut[px[i]!]! / 255;
    const g = lut[px[i + 1]!]! / 255;
    const b = lut[px[i + 2]!]! / 255;
    px[i] = Math.round((m[0]! * r + m[1]! * g + m[2]! * b + m[4]!) * 255);
    px[i + 1] = Math.round((m[5]! * r + m[6]! * g + m[7]! * b + m[9]!) * 255);
    px[i + 2] = Math.round((m[10]! * r + m[11]! * g + m[12]! * b + m[14]!) * 255);
  }
}

/** 自動 (ADR-0057, EDIT-016): exposure and contrast from the preview's brightness alone, no model; it only fills the
 *  sliders, which the person can move. Median brought to the middle; the 2-98 % spread widened when it is narrow. */
export function autoEdit(px: Uint8ClampedArray): Edit {
  const hist = new Array<number>(256).fill(0);
  let n = 0;
  for (let i = 0; i < px.length; i += 4) {
    hist[Math.round(0.2126 * px[i]! + 0.7152 * px[i + 1]! + 0.0722 * px[i + 2]!)]! += 1;
    n++;
  }
  const at = (q: number) => {
    let acc = 0;
    for (let v = 0; v < 256; v++) {
      acc += hist[v]!;
      if (acc >= q * n) return v / 255;
    }
    return 1;
  };
  const out: Edit = { v: 1 };
  if (!n) return out;
  const median = Math.max(at(0.5), 1 / 255);
  const ev = Math.round(Math.min(1.5, Math.max(-1.5, Math.log2(0.5 / median))) * 10) / 10;
  if (Math.abs(ev) >= 0.1) out.exposure = ev;
  const spread = Math.max(at(0.98) - at(0.02), 1 / 255);
  const contrast = Math.round(Math.min(60, Math.max(0, (0.85 / spread - 1) * 100)));
  if (contrast >= 5) out.contrast = contrast;
  return out;
}
