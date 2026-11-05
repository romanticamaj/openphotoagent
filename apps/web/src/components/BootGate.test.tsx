import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../lib/i18n";
import { BootGate } from "./BootGate";

afterEach(() => {
  delete window.__OPA_PLATFORM__;
  vi.useRealTimers();
});

beforeEach(async () => {
  await i18n.changeLanguage("zh-TW");
});

const never = () => new Promise<never>(() => {});

describe("desktop boot (08 §1, DESK-001)", () => {
  it("[DESK-047] 回歸：後端還沒起來時顯示啟動畫面，不是空白視窗", async () => {
    window.__OPA_PLATFORM__ = {
      kind: "desktop",
      backend: never,
      status: async () => ({ fatal: null, detail: null, reconnecting: null, logs: "" }),
    };
    render(<BootGate><p>app</p></BootGate>);
    expect(screen.getByRole("status").textContent).toContain("啟動");
    expect(screen.queryByText("app")).toBeNull();
  });

  it("[DESK-047] 回歸：啟動失敗時說明原因、給 log 位置，並能複製診斷資訊", async () => {
    vi.useFakeTimers();
    window.__OPA_PLATFORM__ = {
      kind: "desktop",
      backend: never,
      status: async () => ({
        fatal: "PG_START_FAILED",
        detail: "PostgreSQL binaries not found",
        reconnecting: null,
        logs: "C:\\data\\logs",
      }),
    };
    render(<BootGate><p>app</p></BootGate>);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(screen.getByRole("alert").textContent).toContain("PG_START_FAILED");
    expect(screen.getByRole("alert").textContent).toContain("PostgreSQL binaries not found");
    expect(screen.getByRole("alert").textContent).toContain("C:\\data\\logs");
    expect(screen.getByRole("button", { name: /複製診斷資訊/ })).toBeTruthy();
  });

  it("[DESK-047] 後端就緒後顯示 app；連線中斷時保留畫面並提示重新連線（DESK-002）", async () => {
    vi.useFakeTimers();
    let reconnecting: string | null = null;
    window.__OPA_PLATFORM__ = {
      kind: "desktop",
      backend: async () => ({ baseUrl: "http://127.0.0.1:1", token: "t" }),
      status: async () => ({ fatal: null, detail: null, reconnecting, logs: "" }),
    };
    render(<BootGate><p>app</p></BootGate>);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(screen.getByText("app")).toBeTruthy();
    expect(screen.queryByTestId("reconnecting")).toBeNull();
    reconnecting = "api";
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(screen.getByText("app")).toBeTruthy();
    expect(screen.getByTestId("reconnecting").textContent).toContain("重新連線");
  });

  it("[DESK-047] Web build 沒有殼，直接顯示 app", () => {
    render(<BootGate><p>app</p></BootGate>);
    expect(screen.getByText("app")).toBeTruthy();
  });
});
