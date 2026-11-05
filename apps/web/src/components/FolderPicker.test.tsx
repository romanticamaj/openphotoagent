import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "../lib/i18n";

const state = vi.hoisted(() => ({ roots: [] as { worker_id: string; alias: string; mode: string }[] }));
vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  api: { roots: async () => state.roots, browse: async () => ({ dirs: [] }) },
}));

import { FolderPicker } from "./FolderPicker";

const ui = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <FolderPicker onPick={() => {}} />
    </QueryClientProvider>,
  );

afterEach(() => {
  delete window.__OPA_PLATFORM__;
  state.roots = [];
});

describe("adding a source on the desktop (invariant 2, C-09)", () => {
  it("[DESK-048] 回歸：桌面版可用系統資料夾對話框新增來源，新 root 出現後可選", async () => {
    const addRoot = vi.fn(async () => {
      state.roots = [{ worker_id: "desktop", alias: "holiday", mode: "ro" }]; // the worker restarts, then registers it
      return "holiday";
    });
    window.__OPA_PLATFORM__ = { kind: "desktop", addRoot };
    ui();
    fireEvent.click(await screen.findByTestId("add-root"));
    await waitFor(() => screen.getByRole("button", { name: "holiday" }), { timeout: 3000 });
    expect(addRoot).toHaveBeenCalledWith("ro");
  });

  it("[DESK-048] 取消對話框時什麼都不變", async () => {
    window.__OPA_PLATFORM__ = { kind: "desktop", addRoot: async () => null };
    ui();
    fireEvent.click(await screen.findByTestId("add-root"));
    await waitFor(() => expect((screen.getByTestId("add-root") as HTMLButtonElement).disabled).toBe(false));
  });

  it("[DESK-048] Web build 沒有這個按鈕：REST 不能新增 root", async () => {
    ui();
    await screen.findByRole("heading");
    expect(screen.queryByTestId("add-root")).toBeNull();
  });
});
