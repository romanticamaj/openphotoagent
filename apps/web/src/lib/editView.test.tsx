import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Edited, editFilter } from "./editView";

describe("an edited photo on screen (ADR-0057)", () => {
  it("[EDIT-007] 編輯畫成一個 SVG filter（色調查表＋色彩矩陣），同一份編輯共用一個；沒有調整就不加", () => {
    expect(editFilter({ v: 1 })).toBeUndefined();
    expect(editFilter(null)).toBeUndefined();
    const a = editFilter({ v: 1, exposure: 0.5, saturation: -20 });
    const b = editFilter({ v: 1, saturation: -20, exposure: 0.5 });
    expect(a).toMatch(/^url\(#opa-e-[a-z0-9]+\)$/);
    expect(b).toBe(a); // the same edit, keys in any order: one filter
    const f = document.getElementById(a!.slice(5, -1))!; // url(#…)
    expect(f.tagName.toLowerCase()).toBe("filter");
    expect(f.getAttribute("color-interpolation-filters")).toBe("sRGB");
    const funcs = f.querySelectorAll("feFuncR, feFuncG, feFuncB");
    expect(funcs).toHaveLength(3);
    expect(funcs[0]!.getAttribute("tableValues")!.split(" ")).toHaveLength(256);
    expect(f.querySelector("feColorMatrix")!.getAttribute("values")!.split(" ")).toHaveLength(20);
    expect(document.querySelectorAll("filter[id^='opa-e-']")).toHaveLength(1);
  });

  it("[EDIT-008] 裁切：照片放大、移位，只露出裁切的部分；沒有編輯時照原樣", () => {
    const { container, rerender } = render(
      <Edited edit={{ v: 1, crop: { x: 0.25, y: 0, w: 0.5, h: 1 } }}>
        <img alt="" />
      </Edited>,
    );
    const inner = container.querySelector<HTMLElement>("[data-edit-inner]")!;
    expect(inner.style.width).toBe("200%");
    expect(inner.style.left).toBe("-50%");
    expect(inner.style.height).toBe("100%");
    rerender(
      <Edited edit={null}>
        <img alt="" />
      </Edited>,
    );
    expect(container.querySelector("[data-edit-inner]")).toBeNull();
    expect(container.querySelector("img")).not.toBeNull();
  });
});
