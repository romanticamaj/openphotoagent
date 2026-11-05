import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import "../../lib/i18n";

const state = vi.hoisted(() => ({ roots: [] as { worker_id: string; alias: string; mode: string }[] }));

vi.mock("@tanstack/react-router", () => ({ Link: (p: { children: unknown }) => p.children }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    roots: async () => state.roots,
    recommendation: async () => ({
      mode: "standard",
      hardware: { os: "windows", gpu: "RTX 5070 Ti", vram_gb: 16, ram_gb: 32 },
      combo: { variants: [], packs: [], download_bytes: 0, download_minutes: 0, estimated: false },
      per_1000_minutes: null,
      licenses: [],
      warnings: [],
    }),
  },
}));

import { Welcome } from "./Welcome";

describe("first start", () => {
  it("[DESK-055] 回歸：worker 比頁面晚註冊時，推薦仍會載入（新安裝卡在「正在檢查你的電腦」）", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Welcome />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /開始|get started/i }));
    await new Promise((r) => setTimeout(r, 50));
    state.roots = [{ worker_id: "desktop", alias: "sorted", mode: "rw" }]; // the worker registers now
    expect(await screen.findByTestId("plan-card", {}, { timeout: 4000 })).toBeTruthy();
  });
});
