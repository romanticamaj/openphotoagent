import { describe, expect, it } from "vitest";
import { cropStyle } from "./FaceCrop";

describe("a face out of a photo", () => {
  it("[FACE-UI-011] 以臉為中心的正方形，留一點邊，不變形", () => {
    // a 1000x500 photo, face at 40%..50% x, 40%..60% y: 100 x 100 px, centre (450, 250)
    const st = cropStyle([0.4, 0.4, 0.1, 0.2], [1000, 500]);
    const side = 100 * 1.6;
    expect(parseFloat(st.width)).toBeCloseTo((1000 * 100) / side, 3);
    expect(parseFloat(st.left)).toBeCloseTo(((side / 2 - 450) * 100) / side, 3);
    expect(parseFloat(st.top)).toBeCloseTo(((side / 2 - 250) * 100) / side, 3);
  });

  it("[FACE-UI-013] 回歸：沒有其他樣式把裁切的圖片拉成正方形（驗證第 2 輪：52 張封面約 35 張不是臉）", async () => {
    const { readFileSync } = await import("node:fs");
    const css = readFileSync(`${process.cwd()}/src/styles.css`, "utf-8");
    // a descendant rule such as ".person-card img" reached the crop's <img> and squared it with object-fit: cover
    expect(css).not.toMatch(/\.person-card img\s*\{/);
    const rule = css.slice(css.indexOf(".face-crop img {"));
    expect(rule.slice(0, rule.indexOf("}"))).toMatch(/aspect-ratio:\s*auto/);
    expect(rule.slice(0, rule.indexOf("}"))).toMatch(/object-fit:\s*fill/);
  });

  it("[FACE-UI-015] 回歸：人物 chip 的頭像不被 .face-crop 的寬度蓋掉（驗證第 5 輪：兩個 chip 疊在一起）", async () => {
    const { readFileSync } = await import("node:fs");
    const css = readFileSync(`${process.cwd()}/src/styles.css`, "utf-8");
    // the chip's face rule must outrank .face-crop { width: 100% }, wherever it sits in the file
    expect(css).toMatch(/\.person-chip \.person-chip-face\s*\{[^}]*width:\s*26px/);
  });
});

