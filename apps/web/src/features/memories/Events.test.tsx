import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ events: [] as unknown[] }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: { memoryEvents: async () => ({ events: s.events }) },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ children, search }: { children: React.ReactNode; search?: Record<string, string> }) => <a data-search={JSON.stringify(search)}>{children}</a>,
}));

import { Events } from "./Events";

describe("活動 (ADR-0049 addendum 2)", () => {
  it("[MEM-017] 每個拍了很多照片的日子一張卡：日期與主題、張數，點封面看那天的照片，「看全部」搜那一天", async () => {
    s.events = [
      { day: "2025-11-13", key: "activity.soccer", name: "足球", count: 16, photos: [{ asset_id: "a", ch: null }, { asset_id: "b", ch: null }] },
      { day: "2026-03-02", key: null, name: null, count: 20, photos: [{ asset_id: "c", ch: null }] },
    ];
    const open = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Events onOpen={open} />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "最近的活動" })).toBeTruthy();
    expect(screen.getByText("2025/11/13 · 足球")).toBeTruthy();
    expect(screen.getByText("2026/3/2")).toBeTruthy(); // no subject: the date alone
    fireEvent.click(screen.getByRole("button", { name: "2025/11/13 · 足球，16 張" }));
    expect(open).toHaveBeenCalledWith([{ id: "a", rot: undefined }, { id: "b", rot: undefined }], 0);
    expect(JSON.parse(screen.getAllByText("看全部")[0].closest("a")?.getAttribute("data-search") ?? "{}")).toEqual({ q: "2025年11月13日" });
  });
});

describe("an outing over days (audit round 99)", () => {
  it("[MEM-020] 跨兩天的活動寫「2026/4/8–4/9 · 手工」，「看全部」搜那兩天", async () => {
    s.events = [{ day: "2026-04-08", last: "2026-04-09", key: "activity.crafts", name: "手工", count: 32, photos: [{ asset_id: "a", ch: null }] }];
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Events onOpen={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("2026/4/8–4/9 · 手工")).toBeTruthy();
    expect(JSON.parse(screen.getByText("看全部").closest("a")?.getAttribute("data-search") ?? "{}")).toEqual({ q: "2026年4月8日到4月9日" });
  });
});

describe("an outing plays as a story (audit round 109)", () => {
  it("[MEM-024] 活動卡有「播放」：從第 1 張開始以幻燈片播放那次活動（驗證第 109 輪：回憶不能當故事看）", async () => {
    s.events = [{ day: "2025-11-11", last: "2025-11-12", key: "activity.soccer", name: "足球", count: 273, photos: [{ asset_id: "a", ch: null }, { asset_id: "b", ch: null }] }];
    const open = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Events onOpen={open} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "播放 2025/11/11–11/12 · 足球" }));
    expect(open).toHaveBeenCalledWith([{ id: "a", rot: undefined }, { id: "b", rot: undefined }], 0, true, "2025/11/11–11/12 · 足球"); // its title opens the story (round 112)
  });
});
