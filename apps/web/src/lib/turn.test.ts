import { describe, expect, it } from "vitest";
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
