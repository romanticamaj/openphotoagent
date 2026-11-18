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

describe("the drawer and the keyboard (audit round 84)", () => {
  it("[VIEW-050] 回歸：開抽屜時焦點進到第一個連結，關上時回到「選單」；視窗變寬就關；點目前這一頁的連結也關", () => {
    const toggle = document.createElement("button");
    const panel = document.createElement("div");
    const link = document.createElement("a");
    link.href = "#";
    panel.appendChild(link);
    document.body.append(toggle, panel);
    let narrow: ((e: { matches: boolean }) => void) | null = null;
    window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener: (_: string, f: (e: { matches: boolean }) => void) => (narrow = f), removeEventListener: () => undefined })) as never;
    const h = renderHook(() => useNavDrawer("/", { toggle: { current: toggle }, panel: { current: panel } }));
    act(() => h.result.current.toggle());
    expect(document.activeElement).toBe(link);
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(document.activeElement).toBe(toggle);
    act(() => h.result.current.toggle());
    act(() => link.click()); // the page you are on: the path does not change, the drawer still closes
    expect(h.result.current.open).toBe(false);
    act(() => h.result.current.toggle());
    act(() => narrow!({ matches: false })); // the window widened past 720 px
    expect(h.result.current.open).toBe(false);
    toggle.remove();
    panel.remove();
  });
});

describe("the open drawer holds focus (audit round 85)", () => {
  it("[VIEW-051] 回歸：抽屜開著時 Tab 在抽屜裡循環，Shift+Tab 從第一個回到最後一個（驗證第 85 輪：設定之後跑到變暗的頁面）", () => {
    const toggle = document.createElement("button");
    const panel = document.createElement("div");
    const a = document.createElement("a");
    a.href = "#a";
    const b = document.createElement("a");
    b.href = "#b";
    panel.append(a, b);
    document.body.append(toggle, panel);
    const h = renderHook(() => useNavDrawer("/", { toggle: { current: toggle }, panel: { current: panel } }));
    act(() => h.result.current.toggle());
    expect(document.activeElement).toBe(a);
    b.focus();
    const tab = new KeyboardEvent("keydown", { key: "Tab", cancelable: true, bubbles: true });
    act(() => {
      b.dispatchEvent(tab);
    });
    expect(tab.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(a);
    const back = new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, cancelable: true, bubbles: true });
    act(() => {
      a.dispatchEvent(back);
    });
    expect(document.activeElement).toBe(b);
    toggle.remove();
    panel.remove();
  });
});
