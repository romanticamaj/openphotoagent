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
