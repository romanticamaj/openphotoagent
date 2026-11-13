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
    // tiles in flow, not stacked: the shared .tile class is absolutely positioned on the timeline (audit round 68)
    for (const b of box.querySelectorAll("button")) expect((b as HTMLElement).style.position).toBe("relative");
    s.result = null;
  });
});


describe("nothing found has a next step (audit round 69)", () => {
  it("[SRCH-UI-030] 回歸：找不到時列出查詢裡找得到照片的詞與張數，可直接點（驗證第 69 輪：19 個查詢只剩一句「找不到」）", async () => {
    s.result = { items: [], facets: {}, total: 0, next_cursor: null, degraded: [], possible_total: 0, possible: [], suggest: [{ q: "山", n: 3 }] };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="爬山" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("找不到符合的照片")).toBeTruthy();
    const box = screen.getByTestId("suggest");
    expect(box.textContent).toContain("試試");
    expect(screen.getByRole("button", { name: "山（3 張以上）" })).toBeTruthy();
    s.result = null;
  });
});

describe("a dead end is not a dead end (audit round 70)", () => {
  it("[SRCH-UI-031] 回歸：找不到時可以把同一句話拿去問 AI；建議詞的張數是下限（驗證第 70 輪：23 個查詢沒有下一步；山 說 1 開出 2）", async () => {
    const asked = vi.fn();
    window.addEventListener("opa:ask", (e) => asked((e as CustomEvent<string>).detail));
    s.result = { items: [], facets: {}, total: 0, next_cursor: null, degraded: [], possible_total: 0, possible: [], suggest: [{ q: "山", n: 1 }] };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="爬山" />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("button", { name: "山（1 張以上）" })).toBeTruthy();
    screen.getByRole("button", { name: "問 AI「爬山」" }).click();
    expect(asked).toHaveBeenCalledWith("爬山");
    s.result = null;
  });
});

describe("part of the question unanswered (audit round 73)", () => {
  it("[SRCH-UI-033] 回歸：有字被放下、仍有照片時，也可以把整句拿去問 AI（驗證第 73 輪：游泳池玩水、搭飛機沒有問 AI）", async () => {
    s.result = {
      items: [{ asset_id: "a1", ch: null, score: 1, matched_by: ["fulltext"], category: null, tags: [], caption: "泳池", taken_at: null }],
      facets: {}, total: 1, next_cursor: null, degraded: [], possible_total: 0, possible: [], relaxed: ["玩水"],
    };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="游泳池玩水" />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("button", { name: "問 AI「游泳池玩水」" })).toBeTruthy();
    s.result = null;
  });
});

describe("no sure match, some possible (audit round 75)", () => {
  it("[SRCH-UI-034] 沒有確定符合、只有可能相關時，也可以把整句拿去問 AI", async () => {
    s.result = {
      items: [], facets: {}, total: 0, next_cursor: null, degraded: [], possible_total: 1,
      possible: [{ asset_id: "p1", ch: null, caption: null, taken_at: null, matched_by: ["image_vector"] }],
    };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="彈跳床" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("沒有確定符合的照片")).toBeTruthy();
    expect(screen.getByRole("button", { name: "問 AI「彈跳床」" })).toBeTruthy();
    s.result = null;
  });
});
