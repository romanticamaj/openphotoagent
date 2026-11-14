import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { askAbout, useAskRequest } from "./askBus";

describe("ask about something from anywhere (audit round 70)", () => {
  it("[ASK-UI-020] askAbout 把問題交給正在聽的人（搜尋找不到時打開問 AI 並帶入問題）", () => {
    const got = vi.fn();
    const h = renderHook(() => useAskRequest(got));
    askAbout("爬山的照片");
    expect(got).toHaveBeenCalledWith("爬山的照片");
    h.unmount();
    askAbout("再一次");
    expect(got).toHaveBeenCalledTimes(1); // no listener left behind
  });
});

describe("a panel that mounts late still gets the question (audit round 71)", () => {
  it("[ASK-UI-021] 回歸：問題在面板掛上之前送出，面板掛上時仍拿得到一次（驗證第 71 輪：34 次都打開空白）", async () => {
    const { takePendingAsk } = await import("./askBus");
    askAbout("爬山");
    expect(takePendingAsk()).toBe("爬山");
    expect(takePendingAsk()).toBeNull(); // once
    const got = vi.fn();
    const h = renderHook(() => useAskRequest(got));
    askAbout("海邊");
    expect(got).toHaveBeenCalledWith("海邊");
    expect(takePendingAsk()).toBeNull(); // a listener took it
    h.unmount();
  });
});
