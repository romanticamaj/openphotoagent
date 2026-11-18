import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ items: [] as unknown[], removed: [] as string[], today: undefined as string | undefined }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    memoryFeedbackList: async () => ({ items: s.items, today: s.today }),
    memoryFeedbackRemove: async (id: string) => void s.removed.push(id),
  },
}));

import { HiddenMemories } from "./HiddenMemories";

describe("hidden memories", () => {
  it("[MEM-048] 回歸：隱藏的回憶與「少一點」都列在分類頁最後，可以還原（驗證第 140 輪：重新整理後就再也找不回來）", async () => {
    s.items = [
      { id: "f1", kind: "hide", ref: "theme:2025:activity.soccer", name: "足球" },
      { id: "f2", kind: "hide", ref: "event:2026-06-22", name: null },
      { id: "f3", kind: "fewer", ref: "tag:mood.smile", name: "笑容" },
    ];
    render(
      <QueryClientProvider client={new QueryClient()}>
        <HiddenMemories />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("已隱藏與少一點的回憶（3）")).toBeTruthy();
    expect(screen.getByText("2025 的足球")).toBeTruthy();
    expect(screen.getByText("2026/6/22 的活動")).toBeTruthy();
    expect(screen.getByText("少一點「笑容」")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: /還原/ })[0]!);
    await vi.waitFor(() => expect(s.removed).toEqual(["f1"]));
  });

  it("[MEM-053] 回歸：「還原」之後焦點到清單的標題（驗證第 141 輪：焦點掉到頁面）", async () => {
    s.items = [{ id: "f9", kind: "hide", ref: "theme:2025:activity.soccer", name: "足球" }];
    render(
      <QueryClientProvider client={new QueryClient()}>
        <HiddenMemories />
      </QueryClientProvider>,
    );
    const summary = await screen.findByText("已隱藏與少一點的回憶（1）");
    fireEvent.click(screen.getByRole("button", { name: /還原/ }));
    await vi.waitFor(() => expect(document.activeElement).toBe(summary));
  });

  it("[MEM-057] 回歸：最後一筆還原後清單消失，焦點到「回憶主題」標題（驗證第 142 輪：焦點掉到頁面）", async () => {
    s.items = [{ id: "f7", kind: "hide", ref: "theme:2025:activity.soccer", name: "足球" }];
    const h = document.createElement("h2");
    h.id = "themes-h";
    h.tabIndex = -1;
    document.body.appendChild(h);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <HiddenMemories />
      </QueryClientProvider>,
    );
    await screen.findByText("已隱藏與少一點的回憶（1）");
    s.items = [];
    fireEvent.click(screen.getByRole("button", { name: /還原/ }));
    await vi.waitFor(() => expect(document.activeElement).toBe(h));
    h.remove();
  });

  it("[MEM-059] 回歸：隱藏清單的名稱和卡片一樣（驗證第 143 輪：卡片「去年的球」，清單寫「2025 的球」）", async () => {
    s.today = "2026-11-18";
    s.items = [
      { id: "f1", kind: "hide", ref: "theme:2025:object.ball", name: "球" },
      { id: "f2", kind: "hide", ref: "event:2026-06-22", name: null },
    ];
    render(
      <QueryClientProvider client={new QueryClient()}>
        <HiddenMemories />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("去年的球")).toBeTruthy();
    expect(screen.getByText("6/22 的活動")).toBeTruthy();
    s.today = undefined;
  });
});
