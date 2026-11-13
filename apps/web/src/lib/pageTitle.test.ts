import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
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

describe("the new page's heading, not the old one (audit round 90)", () => {
  it("[VIEW-056] 回歸：換頁時等新頁面的標題出現再移焦點，視窗標題用它的文字（驗證第 90 輪：焦點到將被移除的舊標題，掉到 BODY）", async () => {
    vi.useFakeTimers();
    const main = document.createElement("main");
    main.id = "main";
    const old = document.createElement("h1");
    old.textContent = "分類";
    main.appendChild(old);
    document.body.appendChild(main);
    const t = (k: string) => ({ "nav.explore": "分類", "nav.search": "搜尋" })[k] ?? k;
    const h = renderHook(({ path }) => usePageArrival(path, t), { initialProps: { path: "/explore" } });
    h.rerender({ path: "/search" });
    expect(document.activeElement).not.toBe(old); // the old page's heading is on its way out
    const fresh = document.createElement("h1");
    fresh.textContent = "2026 的手工";
    old.replaceWith(fresh);
    await vi.advanceTimersByTimeAsync(200);
    expect(document.activeElement).toBe(fresh);
    expect(document.title).toBe("2026 的手工 — OpenPhotoAgent");
    main.remove();
    vi.useRealTimers();
  });
});

describe("arriving after a reload (audit round 91)", () => {
  it("[VIEW-057] 回歸：第一頁的標題稍後才畫出時也記得它；標題文字之後才變好，視窗標題跟著變（驗證第 91 輪：重新整理後 0/33）", async () => {
    vi.useFakeTimers();
    const main = document.createElement("main");
    main.id = "main";
    document.body.appendChild(main);
    const t = (k: string) => ({ "nav.explore": "分類", "nav.search": "搜尋" })[k] ?? k;
    const h = renderHook(({ path }) => usePageArrival(path, t), { initialProps: { path: "/explore" } });
    const first = document.createElement("h1"); // the first page draws its heading after the hook ran
    first.textContent = "分類";
    main.appendChild(first);
    await vi.advanceTimersByTimeAsync(200);
    h.rerender({ path: "/search" });
    await vi.advanceTimersByTimeAsync(60);
    expect(document.activeElement).not.toBe(first);
    const next = document.createElement("h1");
    next.textContent = "2026 的activity.crafts";
    first.replaceWith(next);
    await vi.advanceTimersByTimeAsync(100);
    expect(document.activeElement).toBe(next);
    next.textContent = "2026 的手工"; // the names arrive a moment later
    await vi.advanceTimersByTimeAsync(100);
    expect(document.title).toBe("2026 的手工 — OpenPhotoAgent");
    main.remove();
    vi.useRealTimers();
  });
});

describe("people remember the person you came from, only from them (audit rounds 94-95)", () => {
  it("[VIEW-058] 回歸：到人物以外的頁面就忘了上次看的人，從側欄進人物時焦點到標題（驗證第 94～95 輪：焦點到上次看的人物卡）", () => {
    window.sessionStorage.setItem("opa.people.last", "p1");
    const t = (k: string) => k;
    const h = renderHook(({ path }) => usePageArrival(path, t), { initialProps: { path: "/people/p1" } });
    expect(window.sessionStorage.getItem("opa.people.last")).toBe("p1"); // back to the wall finds it
    h.rerender({ path: "/explore" });
    expect(window.sessionStorage.getItem("opa.people.last")).toBeNull();
  });
});
