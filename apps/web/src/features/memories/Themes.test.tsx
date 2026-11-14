import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ themes: [] as unknown[] }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: { memoryThemes: async () => ({ themes: s.themes }) },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ children, search }: { children: React.ReactNode; search?: Record<string, string> }) => <a data-search={JSON.stringify(search)}>{children}</a>,
}));

import { Themes } from "./Themes";

describe("主題 (10 §2.8)", () => {
  it("[MEM-011] 每個主題一張卡：「2025 的足球」與張數，點封面看那些照片，可到搜尋看全部；沒有主題時不顯示", async () => {
    s.themes = [
      { year: 2025, key: "activity.soccer", name: "足球", count: 120, photos: [{ asset_id: "a", ch: null, taken_at: null }, { asset_id: "b", ch: null, taken_at: null }] },
    ];
    const open = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Themes onOpen={open} />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "回憶主題" })).toBeTruthy();
    const card = screen.getByRole("button", { name: "2025 的足球，120 張" });
    fireEvent.click(card);
    expect(open).toHaveBeenCalledWith([{ id: "a", rot: undefined }, { id: "b", rot: undefined }], 0);
    // the tag and the year, not the words: the same photos the count is of (MEM-013, audit round 80: 29 → 12 → 33)
    expect(JSON.parse(screen.getByText("看全部").closest("a")?.getAttribute("data-search") ?? "{}")).toEqual({ q: "", tag: "activity.soccer", year: "2025", name: "足球" });
    expect(screen.getByText("120 張 · 精選 2 張")).toBeTruthy();
    // the title on a line of its own, not beside the count (audit round 82: every title read 「2025 的…」)
    const title = screen.getByText("2025 的足球");
    expect(title.tagName).toBe("P");
    expect(title.textContent).toBe("2025 的足球");
  });
});

describe("a theme plays as a story (audit round 109)", () => {
  it("[MEM-025] 主題卡有「播放」：從第 1 張開始以幻燈片播放", async () => {
    s.themes = [{ year: 2025, key: "activity.soccer", name: "足球", count: 120, photos: [{ asset_id: "a", ch: null, taken_at: null }] }];
    const open = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Themes onOpen={open} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "播放 2025 的足球" }));
    expect(open).toHaveBeenCalledWith([expect.objectContaining({ id: "a" })], 0, true, "2025 的足球");
  });
});
