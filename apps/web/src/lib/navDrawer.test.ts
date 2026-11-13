import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useNavDrawer } from "./navDrawer";

describe("the sidebar in a narrow window (audit round 83)", () => {
  it("[VIEW-049] 回歸：窄視窗時側欄是可開關的抽屜：按鈕開關、Esc 關、換頁就關（驗證第 83 輪：420 px 時側欄不收）", () => {
    const h = renderHook(({ path }) => useNavDrawer(path), { initialProps: { path: "/" } });
    expect(h.result.current.open).toBe(false);
    act(() => h.result.current.toggle());
    expect(h.result.current.open).toBe(true);
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(h.result.current.open).toBe(false);
    act(() => h.result.current.toggle());
    h.rerender({ path: "/explore" });
    expect(h.result.current.open).toBe(false); // a link was followed: the page is what you wanted to see
  });
});
