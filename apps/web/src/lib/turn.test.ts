import { describe, expect, it } from "vitest";
import { layoutKey, shapesOf } from "../features/viewer/PhotoGrid";
import { turnStyle } from "./turn";

describe("a photo turned in the app (ROT-002)", () => {
  it("[ROT-002] 轉 90／270 度時圖片尺寸對調後置中旋轉，剛好填滿已對調形狀的格子；180 度只旋轉；0 度不加樣式", () => {
    expect(turnStyle(0, 300, 400)).toEqual({});
    expect(turnStyle(180, 300, 400)).toEqual({ transform: "rotate(180deg)" });
    expect(turnStyle(90, 300, 400)).toMatchObject({
      position: "absolute",
      width: "400px",
      height: "300px",
      left: "50%",
      top: "50%",
      transform: "translate(-50%, -50%) rotate(90deg)",
    });
    expect(turnStyle(270, 300, 400).transform).toContain("rotate(270deg)");
    expect(turnStyle(90).transform).toBe("rotate(90deg)"); // a square box: turning is enough
  });
});

describe("a turned photo re-lays its month (ROT-004)", () => {
  it("[ROT-004] 回歸：月份資料更新時重新排版（轉過的照片在時間軸上要等重新整理才變形狀）", () => {
    const before = shapesOf([{ w: 4000, h: 3000 }, { w: 3000, h: 4000 }]);
    const turned = shapesOf([{ w: 3000, h: 4000 }, { w: 3000, h: 4000 }]);
    expect(layoutKey("2025-03", 1200, 200, before)).not.toBe(layoutKey("2025-03", 1200, 200, turned));
    // a refetch with the same shapes keeps its layout: tiles do not blink out while analysis refreshes the timeline
    expect(shapesOf([{ w: 4000, h: 3000 }, { w: 3000, h: 4000 }])).toBe(before);
  });
});

