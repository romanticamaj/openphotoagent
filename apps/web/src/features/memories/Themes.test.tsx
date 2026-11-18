import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ themes: [] as unknown[], sent: [] as unknown[], removed: [] as string[] }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: {
    memoryThemes: async () => ({ themes: s.themes }),
    memoryFeedback: async (kind: string, ref: string) => (s.sent.push({ kind, ref }), { id: `f${s.sent.length}`, kind, ref }),
    memoryFeedbackRemove: async (id: string) => void s.removed.push(id),
  },
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
    expect(open).toHaveBeenCalledWith([expect.objectContaining({ id: "a" })], 0, true, "2025 的足球", {
      n: 120,
      search: { q: "", tag: "activity.soccer", year: "2025", name: "足球" },
    });
  });

  it("[MEM-042] 回歸：卡片的「⋯」可以隱藏這個回憶或少一點這個主題，隱藏後可以復原（驗證第 138 輪：沒有隱藏也沒有「少一點」）", async () => {
    s.themes = [{ year: 2025, key: "activity.soccer", name: "足球", count: 120, photos: [{ asset_id: "a", ch: null, taken_at: null }] }];
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Themes onOpen={vi.fn()} />
      </QueryClientProvider>,
    );
    const more = await screen.findByRole("button", { name: "更多：2025 的足球" });
    expect(more.getAttribute("aria-haspopup")).toBe("menu");
    fireEvent.click(more);
    fireEvent.click(screen.getByRole("menuitem", { name: "隱藏這個回憶" }));
    await screen.findByText("已隱藏「2025 的足球」");
    expect(s.sent).toEqual([{ kind: "hide", ref: "theme:2025:activity.soccer" }]);
    fireEvent.click(screen.getByRole("button", { name: "復原" }));
    await vi.waitFor(() => expect(s.removed).toEqual(["f1"]));
    fireEvent.click(screen.getByRole("button", { name: "更多：2025 的足球" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "少一點「足球」" }));
    await vi.waitFor(() => expect(s.sent[1]).toEqual({ kind: "fewer", ref: "tag:activity.soccer" }));
  });

  it("[MEM-047] 回歸：選單可以用 ↓ ↑ 移動；隱藏後焦點到「復原」（驗證第 140 輪：↓ 沒有作用、焦點掉到頁面）", async () => {
    s.themes = [{ year: 2025, key: "activity.soccer", name: "足球", count: 120, photos: [{ asset_id: "a", ch: null, taken_at: null }] }];
    s.sent = [];
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Themes onOpen={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "更多：2025 的足球" }));
    const [hide, fewer] = screen.getAllByRole("menuitem");
    await vi.waitFor(() => expect(document.activeElement).toBe(hide));
    fireEvent.keyDown(hide!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(fewer);
    fireEvent.keyDown(fewer!, { key: "ArrowUp" });
    expect(document.activeElement).toBe(hide);
    fireEvent.click(hide!);
    await vi.waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "復原" })));
  });

  it("[MEM-053] 回歸：「復原」之後焦點到這一區的標題，不落到頁面（驗證第 141 輪）", async () => {
    s.themes = [{ year: 2025, key: "activity.soccer", name: "足球", count: 120, photos: [{ asset_id: "a", ch: null, taken_at: null }] }];
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Themes onOpen={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "更多：2025 的足球" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "隱藏這個回憶" }));
    fireEvent.click(await screen.findByRole("button", { name: "復原" }));
    await vi.waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { name: "回憶主題" })));
  });

  it("[MEM-057] 回歸：選單的 Home／End 到第一項、最後一項（驗證第 142 輪：End 沒有作用）", async () => {
    s.themes = [{ year: 2025, key: "activity.soccer", name: "足球", count: 120, photos: [{ asset_id: "a", ch: null, taken_at: null }] }];
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Themes onOpen={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "更多：2025 的足球" }));
    const [first, last] = screen.getAllByRole("menuitem");
    await vi.waitFor(() => expect(document.activeElement).toBe(first));
    fireEvent.keyDown(first!, { key: "End" });
    expect(document.activeElement).toBe(last);
    fireEvent.keyDown(last!, { key: "Home" });
    expect(document.activeElement).toBe(first);
  });
});
