import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ result: null as unknown }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: {
    search: () => (s.result ? Promise.resolve(s.result) : new Promise(() => undefined)), // still searching unless set
    searchIndex: async () => ({ total: 10, searchable: 10 }),
    preset: async () => ({ categories: [] }),
    roots: async () => [],
  },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));

import { SearchPage } from "./SearchPage";

describe("search while it runs (audit round 40)", () => {
  it("[SRCH-UI-020] 回歸：第一次搜尋還沒回來時，顯示正在搜尋與佔位格（驗證第 40 輪：約 3 秒什麼都沒有）", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="海邊" />
      </QueryClientProvider>,
    );
    expect((await screen.findByRole("status", { name: "搜尋中" })).textContent).toContain("正在搜尋");
    expect(screen.getByTestId("search-skeleton").getAttribute("aria-busy")).toBe("true");
  });
});

describe("「可能相關」 apart from the count (ADR-0047)", () => {
  it("[SRCH-UI-029] 沒有確定符合時說「沒有確定符合的照片」，下面收起「可能相關 N 張」，不算進張數", async () => {
    s.result = {
      items: [],
      facets: {},
      total: 0,
      next_cursor: null,
      degraded: [],
      possible_total: 2,
      possible: [
        { asset_id: "p1", ch: null, caption: null, taken_at: null, matched_by: ["image_vector"] },
        { asset_id: "p2", ch: null, caption: null, taken_at: null, matched_by: ["image_vector"] },
      ],
    };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="吃飯" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("沒有確定符合的照片")).toBeTruthy();
    const box = screen.getByTestId("possible");
    expect(box.tagName).toBe("DETAILS");
    expect(box.querySelector("summary")?.textContent).toContain("可能相關 2 張");
    expect(box.querySelectorAll("img")).toHaveLength(2);
    s.result = null;
  });
});

