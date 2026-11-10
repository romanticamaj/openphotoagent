import { describe, expect, it } from "vitest";
import { faceBoxStyle } from "./faceBoxes";

describe("faces marked on the photo", () => {
  it("[VIEW-026] 回歸：人臉框落在照片實際顯示的位置（照片等比例置中）（驗證第 3 輪：團體照看不出要找的人在哪）", () => {
    // a 4:3 photo in a 1000x500 stage: drawn 666.67 x 500, starting at x = 166.67
    const st = faceBoxStyle([0.25, 0.5, 0.1, 0.2], { w: 1000, h: 500 }, { w: 4000, h: 3000 });
    expect(st.left).toBeCloseTo(166.667 + 0.25 * 666.667, 2);
    expect(st.top).toBeCloseTo(0.5 * 500, 2);
    expect(st.width).toBeCloseTo(0.1 * 666.667, 2);
    expect(st.height).toBeCloseTo(0.2 * 500, 2);
  });
});
