import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { pageTitleKey, usePageArrival } from "./pageTitle";

describe("each page says where you are (audit round 89)", () => {
  it("[VIEW-054] 回歸：視窗標題跟著頁面；換頁後焦點到頁面標題（驗證第 89 輪：標題從不變、焦點在 BODY）", () => {
    expect(pageTitleKey("/")).toBe("nav.photos");
    expect(pageTitleKey("/explore")).toBe("nav.explore");
    expect(pageTitleKey("/people/abc")).toBe("nav.people");
    expect(pageTitleKey("/search")).toBe("nav.search");
    expect(pageTitleKey("/nowhere")).toBeNull();
    const main = document.createElement("main");
    main.id = "main";
    const h1 = document.createElement("h1");
    h1.textContent = "分類";
    main.appendChild(h1);
    document.body.appendChild(main);
    const h = renderHook(({ path }) => usePageArrival(path, (k) => ({ "nav.explore": "分類", "nav.photos": "照片" })[k] ?? k), { initialProps: { path: "/" } });
    expect(document.title).toBe("照片 — OpenPhotoAgent");
    h.rerender({ path: "/explore" });
    expect(document.title).toBe("分類 — OpenPhotoAgent");
    expect(document.activeElement).toBe(h1);
    main.remove();
  });
});
