import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ items: [] as unknown[], removed: [] as string[] }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    memoryFeedbackList: async () => ({ items: s.items }),
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
});
