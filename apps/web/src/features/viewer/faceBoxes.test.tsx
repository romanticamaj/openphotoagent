import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FaceBoxes, faceBoxStyle } from "./faceBoxes";

vi.mock("@tanstack/react-router", () => ({ Link: ({ children, className }: { children: React.ReactNode; className?: string }) => <a className={className}>{children}</a> }));

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

describe("faces marked on the photo", () => {
  it("[VIEW-026] 回歸：人臉框落在照片實際顯示的位置（照片等比例置中）（驗證第 3 輪：團體照看不出要找的人在哪）", () => {
    // a 4:3 photo in a 1000x500 stage: drawn 666.67 x 500, starting at x = 166.67
    const st = faceBoxStyle([0.25, 0.5, 0.1, 0.2], { w: 1000, h: 500 }, { w: 4000, h: 3000 });
    expect(st.left).toBeCloseTo(166.667 + 0.25 * 666.667, 2);
    expect(st.top).toBeCloseTo(0.5 * 500, 2);
    expect(st.width).toBeCloseTo(0.1 * 666.667, 2);
    expect(st.height).toBeCloseTo(0.2 * 500, 2);
  });

  it("[VIEW-029] 回歸：不屬於任何人物的臉也框出來，太小的用另一種框（驗證第 7 輪）", () => {
    const { container } = render(
      <FaceBoxes
        photo={{ w: 4, h: 3 }}
        people={[{ id: "p1", name: "小明", bbox: [0.1, 0.1, 0.2, 0.2] }]}
        unknown={[{ bbox: [0.5, 0.5, 0.1, 0.1], small: false }, { bbox: [0.7, 0.2, 0.03, 0.03], small: true }]}
      />,
    );
    expect(container.querySelectorAll("a.face-box")).toHaveLength(1);
    expect(container.querySelectorAll(".face-box-unknown")).toHaveLength(2);
    expect(container.querySelectorAll(".face-box-unknown.is-small")).toHaveLength(1);
  });
});
