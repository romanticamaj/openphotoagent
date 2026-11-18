import { describe, expect, it } from "vitest";
import { applyEdit, colorMatrix, coverScale, isIdentity, toneLut, type Edit } from "./editMath";

const ID_MATRIX = [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0];

describe("edit maths (ADR-0057)", () => {
  it("[EDIT-004] 沒有調整時是恆等：查表 x→x、矩陣是單位矩陣", () => {
    const e: Edit = { v: 1 };
    expect(isIdentity(e)).toBe(true);
    const lut = toneLut(e);
    expect(lut.every((v, i) => v === i)).toBe(true);
    expect(colorMatrix(e)).toEqual(ID_MATRIX);
    expect(isIdentity({ v: 1, crop: { x: 0, y: 0, w: 1, h: 1 } })).toBe(true);
    expect(isIdentity({ v: 1, exposure: 0.3 })).toBe(false);
  });

  it("[EDIT-005] 曝光 +1 EV 讓中間調加倍（上限 255）；對比加大讓暗的更暗、亮的更亮；黑白濾鏡 R=G=B", () => {
    const up = toneLut({ v: 1, exposure: 1 });
    expect(up[64]).toBe(128);
    expect(up[200]).toBe(255);
    const c = toneLut({ v: 1, contrast: 50 });
    expect(c[64]!).toBeLessThan(64);
    expect(c[192]!).toBeGreaterThan(192);
    expect(c[128]).toBe(128); // the middle stays
    const px = new Uint8ClampedArray([200, 80, 30, 255]);
    applyEdit(px, { v: 1, filter: { name: "mono", strength: 100 } });
    expect(px[0]).toBe(px[1]);
    expect(px[1]).toBe(px[2]);
    expect(px[3]).toBe(255); // alpha untouched
    const warm = new Uint8ClampedArray([100, 100, 100, 255]);
    applyEdit(warm, { v: 1, warmth: 60 });
    expect(warm[0]!).toBeGreaterThan(warm[2]!); // warmer: more red than blue
  });

  it("[EDIT-006] 拉直時放大到剛好沒有空白角（任何角度）", () => {
    for (const deg of [0, 1, 5, 10, 25, 45, -10]) {
      for (const [w, h] of [[4000, 3000], [3000, 4000], [1000, 1000]] as const) {
        const s = coverScale(deg, w, h);
        const a = (Math.abs(deg) * Math.PI) / 180;
        // the frame turned into the photo's axes must fit inside the scaled photo
        expect(s * w + 1e-6).toBeGreaterThanOrEqual(w * Math.cos(a) + h * Math.sin(a));
        expect(s * h + 1e-6).toBeGreaterThanOrEqual(w * Math.sin(a) + h * Math.cos(a));
        expect(s).toBeGreaterThanOrEqual(1);
        if (deg === 0) expect(s).toBe(1);
      }
    }
    // a known case: a square turned 45° needs √2
    expect(coverScale(45, 1000, 1000)).toBeCloseTo(Math.SQRT2, 6);
  });
});

describe("自動 (ADR-0057)", () => {
  const img = (f: (i: number) => number, n = 1000) => {
    const px = new Uint8ClampedArray(n * 4);
    for (let i = 0; i < n; i++) {
      const v = f(i);
      px.set([v, v, v, 255], i * 4);
    }
    return px;
  };
  it("[EDIT-016] 「自動」只依預覽的亮度分佈填入滑桿：太暗就提亮、灰濛濛就加對比、正常的幾乎不動；同一張每次一樣", async () => {
    const { autoEdit } = await import("./editMath");
    const dark = autoEdit(img((i) => 10 + (i % 60)));
    expect(dark.exposure!).toBeGreaterThan(0.3);
    const flat = autoEdit(img((i) => 110 + (i % 40)));
    expect(flat.contrast!).toBeGreaterThan(20);
    const fine = autoEdit(img((i) => Math.round((i / 999) * 255)));
    expect(Math.abs(fine.exposure ?? 0)).toBeLessThan(0.15);
    expect(Math.abs(fine.contrast ?? 0)).toBeLessThan(10);
    expect(autoEdit(img((i) => 10 + (i % 60)))).toEqual(dark);
    expect(dark.v).toBe(1);
  });
});
