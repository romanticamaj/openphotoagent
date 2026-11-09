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
});
