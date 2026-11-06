import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ search: vi.fn(), total: 12 }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: {
    search: s.search,
    buckets: async () => ({ buckets: [{ month: "2024-04", count: s.total }] }),
    preset: async () => ({
      categories: [
        { key: "landscape", name: "風景" },
        { key: "food", name: "美食" },
      ],
    }),
  },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}));

import { ExplorePage } from "./ExplorePage";

const hit = (id: string) => ({ asset_id: id, ch: null, score: 1, matched_by: [], category: "food", tags: [], caption: null, taken_at: null });
const result = (items: unknown[], facets = {}) => ({ items, facets, total: items.length, next_cursor: null, degraded: [] });
const ui = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ExplorePage />
    </QueryClientProvider>,
  );

describe("seeing what the AI found (ADR-0029)", () => {
  it("[IMP-020] 分類與數量一眼看得到，多到少排列，並標出還沒分析的張數", async () => {
    s.search.mockReset();
    s.search.mockResolvedValue(result([hit("a")], { category: { food: 3, landscape: 7 }, tags: { 海邊: 2 } }));
    ui();
    const chips = await screen.findAllByRole("button", { pressed: false });
    await waitFor(() => expect(screen.getByRole("button", { name: /風景/ })).toBeTruthy());
    const names = chips.map((c) => c.textContent);
    expect(names.findIndex((n) => n?.includes("風景"))).toBeLessThan(names.findIndex((n) => n?.includes("美食")));
    expect(screen.getByRole("button", { name: /海邊/ })).toBeTruthy();
    expect(screen.getByTestId("explore-summary").textContent).toContain("10");
    expect(screen.getByTestId("explore-summary").textContent).toContain("2");
  });

  it("[IMP-020] 點分類就篩選照片", async () => {
    s.search.mockReset();
    s.search.mockResolvedValue(result([hit("a")], { category: { food: 3 } }));
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /美食/, pressed: false }));
    await waitFor(() => expect(s.search.mock.calls.some(([b]) => b.filters?.category === "food")).toBe(true));
    expect(await screen.findByRole("button", { name: /美食/, pressed: true })).toBeTruthy();
  });

  it("[IMP-020] 還沒有分析結果時說明，並帶去匯入", async () => {
    s.search.mockReset();
    s.search.mockResolvedValue(result([], {}));
    ui();
    await screen.findByText(/還沒有分析結果/);
    expect(screen.getByRole("link", { name: "匯入照片" }).getAttribute("href")).toBe("/import");
  });
});
