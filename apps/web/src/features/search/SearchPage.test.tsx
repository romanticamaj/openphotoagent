import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ result: null as unknown, calls: 0, last: null as unknown, nav: null as unknown as (a: unknown) => void }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: {
    search: (b: unknown) => {
      s.calls += 1;
      s.last = b;
      return s.result ? Promise.resolve(s.result) : new Promise(() => undefined); // still searching unless set
    },
    searchIndex: async () => ({ total: 10, searchable: 10 }),
    preset: async () => ({ categories: [] }),
    roots: async () => [],
  },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  useNavigate: () => (a: unknown) => s.nav?.(a),
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

describe("one search per query (audit round 76)", () => {
  it("[SRCH-UI-035] 回歸：規則解析後不再重搜同一個答案（驗證第 76 輪：每個查詢打兩次 /search，等待時間加倍）", async () => {
    s.calls = 0;
    s.result = {
      items: [{ asset_id: "a1", ch: null, score: 1, matched_by: ["tag"], category: null, tags: [], caption: "貓", taken_at: null }],
      facets: {}, total: 1, next_cursor: null, degraded: [], possible_total: 0, possible: [],
      planner: { source: "rules", confidence: 0.6, explain: [], text: "貓", filters: {}, unapplied: [] },
    };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="貓" />
      </QueryClientProvider>,
    );
    await screen.findByText("1 張");
    await new Promise((r) => setTimeout(r, 50));
    expect(s.calls).toBe(1);
    s.result = null;
  });
});

describe("one way to say none (audit round 76)", () => {
  it("[SRCH-UI-036] 回歸：只有可能相關時，標題旁不再寫「0 張」（驗證第 76 輪）", async () => {
    s.result = {
      items: [], facets: {}, total: 0, next_cursor: null, degraded: [], possible_total: 1,
      possible: [{ asset_id: "p1", ch: null, caption: null, taken_at: null, matched_by: ["image_vector"] }],
    };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="博物館" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("沒有確定符合的照片")).toBeTruthy();
    expect(screen.queryByTestId("result-count")).toBeNull();
    s.result = null;
  });
});

describe("a theme's photos (audit round 80)", () => {
  it("[SRCH-UI-037] 回歸：/search 帶 tag 與 year 時，以該標籤與年份篩選，不靠文字（回憶主題的「看全部」）", async () => {
    s.result = { items: [], facets: {}, total: 0, next_cursor: null, degraded: [], possible_total: 0, possible: [] };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="" tag="activity.soccer" year="2025" />
      </QueryClientProvider>,
    );
    await screen.findByText("找不到符合的照片");
    const b = s.last as { q: unknown; filters: Record<string, unknown> };
    expect(b.q).toBeNull();
    expect(b.filters).toMatchObject({ tags_all: ["activity.soccer"], taken_from: "2025-01-01", taken_to: "2025-12-31" });
    s.result = null;
  });
});

describe("a theme's chip is named (audit round 81)", () => {
  it("[SRCH-UI-038] 回歸：從主題來的標籤晶片顯示名稱「手工」，不是 activity.crafts", async () => {
    s.result = { items: [], facets: {}, total: 0, next_cursor: null, degraded: [], possible_total: 0, possible: [], tag_names: { "activity.crafts": "手工" } };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="" tag="activity.crafts" year="2026" />
      </QueryClientProvider>,
    );
    await screen.findByText("找不到符合的照片");
    expect(screen.getByText("手工")).toBeTruthy();
    expect(screen.queryByText("activity.crafts")).toBeNull();
    // the page is the theme's, not 「搜尋結果」 (audit round 83)
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("2026 的手工");
    s.result = null;
  });
});

describe("a theme's title is its chips' (audit round 84)", () => {
  it("[SRCH-UI-039] 回歸：移除主題的標籤或年份晶片後，標題回到「搜尋結果」", async () => {
    s.result = { items: [], facets: {}, total: 0, next_cursor: null, degraded: [], possible_total: 0, possible: [], tag_names: { "activity.crafts": "手工" } };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="" tag="activity.crafts" year="2026" />
      </QueryClientProvider>,
    );
    await screen.findByText("找不到符合的照片");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("2026 的手工");
    screen.getByRole("button", { name: "移除 手工" }).click();
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("搜尋結果"));
    s.result = null;
  });
});

describe("a theme's filters live in the address (audit round 85)", () => {
  it("[SRCH-UI-040] 回歸：移除從網址來的標籤或年份晶片，網址跟著改（驗證第 85 輪：重新整理又出現、上一頁跳回分類）", async () => {
    const went: unknown[] = [];
    s.nav = (a) => went.push(a);
    s.result = { items: [], facets: {}, total: 0, next_cursor: null, degraded: [], possible_total: 0, possible: [], tag_names: { "activity.crafts": "手工" } };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="" tag="activity.crafts" year="2026" />
      </QueryClientProvider>,
    );
    await screen.findByText("找不到符合的照片");
    screen.getByRole("button", { name: "移除 手工" }).click();
    expect(went).toEqual([{ to: "/search", search: { q: "", year: "2026" } }]);
    s.result = null;
    s.nav = null as unknown as (a: unknown) => void;
  });
});

describe("load more keeps your place (audit round 85)", () => {
  it("[SRCH-UI-041] 回歸：按「載入更多」後焦點到第一張新照片（驗證第 85 輪：焦點掉到頁面）", async () => {
    const hit = (id: string) => ({ asset_id: id, ch: null, score: 1, matched_by: ["tag"], category: null, tags: [], caption: id, taken_at: null });
    s.result = { items: [hit("a1"), hit("a2")], facets: {}, total: 3, next_cursor: "c1", degraded: [], possible_total: 0, possible: [] };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="貓" />
      </QueryClientProvider>,
    );
    const more = await screen.findByRole("button", { name: /載入更多/ });
    s.result = { items: [hit("a3")], facets: {}, total: 3, next_cursor: null, degraded: [], possible_total: 0, possible: [] };
    more.click();
    await waitFor(() => expect((document.activeElement as HTMLElement | null)?.dataset.asset).toBe("a3"), { timeout: 3000 }); // a page and a frame: slow under a full run
    s.result = null;
  });
});

describe("what browsing folds, and where focus goes (audit round 86)", () => {
  it("[SRCH-UI-042] 回歸：瀏覽時說出幾張近似重複收在代表照片裡（驗證第 86 輪：845 張的圖庫寫 665 張）", async () => {
    const hit = (id: string) => ({ asset_id: id, ch: null, score: 1, matched_by: [], category: null, tags: [], caption: id, taken_at: null });
    s.result = { items: [hit("a1")], facets: {}, total: 665, collapsed: 180, next_cursor: null, degraded: [], possible_total: 0, possible: [] };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="" tag="activity.crafts" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/另有 180 張近似重複/)).toBeTruthy();
    s.result = null;
  });

  it("[SRCH-UI-043] 回歸：移除晶片後焦點不掉到頁面（驗證第 86 輪：焦點到 BODY）", async () => {
    s.nav = () => undefined;
    s.result = { items: [], facets: {}, total: 0, next_cursor: null, degraded: [], possible_total: 0, possible: [], tag_names: { "activity.crafts": "手工" } };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="" tag="activity.crafts" year="2026" />
      </QueryClientProvider>,
    );
    await screen.findByText("找不到符合的照片");
    const x = screen.getByRole("button", { name: "移除 手工" });
    x.focus();
    x.click();
    await waitFor(() => expect(document.activeElement?.getAttribute("aria-label")).toBe("移除 2026"));
    s.result = null;
    s.nav = null as unknown as (a: unknown) => void;
  });
});

describe("a search page says what it searched (audit round 91)", () => {
  it("[SRCH-UI-044] 回歸：文字查詢的頁面標題帶著查詢字（驗證第 91 輪：只寫「搜尋結果」）", async () => {
    s.result = { items: [], facets: {}, total: 0, next_cursor: null, degraded: [], possible_total: 0, possible: [] };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="海邊" />
      </QueryClientProvider>,
    );
    await screen.findByText("找不到符合的照片");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("「海邊」的搜尋結果");
    s.result = null;
  });
});

describe("a theme's title from the start (audit round 92)", () => {
  it("[SRCH-UI-045] 回歸：從主題卡片來時，名稱跟著網址，標題一開始就是「2026 的手工」（驗證第 92 輪：先閃 activity.crafts）", async () => {
    s.result = null; // the answer has not come yet
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="" tag="activity.crafts" year="2026" name="手工" />
      </QueryClientProvider>,
    );
    expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("2026 的手工");
  });
});

describe("load more says when it fails (audit round 102)", () => {
  it("[SRCH-UI-046] 回歸：載入更多失敗時說出來並可重試（驗證第 102 輪：按了沒反應，還寫「還有 324 張」）", async () => {
    const hit = (id: string) => ({ asset_id: id, ch: null, score: 1, matched_by: ["tag"], category: null, tags: [], caption: id, taken_at: null });
    s.result = { items: [hit("a1")], facets: {}, total: 3, next_cursor: "c1", degraded: [], possible_total: 0, possible: [] };
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <SearchPage q="貓" />
      </QueryClientProvider>,
    );
    const more = await screen.findByRole("button", { name: /載入更多/ });
    s.result = Promise.reject(new Error("CURSOR_INVALID")) as unknown;
    (s.result as Promise<unknown>).catch(() => undefined);
    more.click();
    expect(await screen.findByText("沒能載入更多照片", {}, { timeout: 4000 })).toBeTruthy(); // after one retry, ~1 s
    expect(screen.getByRole("button", { name: "重試" })).toBeTruthy();
    // one way to try again, not two buttons (audit round 103)
    expect(screen.queryByRole("button", { name: /載入更多/ })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "重試" })); // focus goes with it (round 104)
    expect(screen.getByRole("alert").textContent).toBe("沒能載入更多照片"); // the button is not read as part of it (round 106)
    s.result = null;
  });
});

describe("load more shows it is working (audit round 103)", () => {
  it("[SRCH-UI-047] 回歸：載入中按鈕寫「載入中…」；失敗不重試三次才說（驗證第 103 輪：約 7 秒沒有任何反應）", async () => {
    const { searchRetry } = await import("./SearchPage");
    expect(searchRetry).toBeLessThanOrEqual(1);
    const hit = (id: string) => ({ asset_id: id, ch: null, score: 1, matched_by: ["tag"], category: null, tags: [], caption: id, taken_at: null });
    s.result = { items: [hit("a1")], facets: {}, total: 3, next_cursor: "c1", degraded: [], possible_total: 0, possible: [] };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="貓" />
      </QueryClientProvider>,
    );
    const more = await screen.findByRole("button", { name: /載入更多/ });
    s.result = null; // the next page does not come yet
    more.click();
    expect(await screen.findByRole("button", { name: "載入中…" })).toBeTruthy();
  });
});

describe("retry takes focus and shows it works (audit round 104)", () => {
  it("[SRCH-UI-048] 回歸：載入失敗時焦點到「重試」，重試中寫「重試中…」；載入中再按不會多送（驗證第 104 輪）", async () => {
    let asked = 0;
    const hit = (id: string) => ({ asset_id: id, ch: null, score: 1, matched_by: ["tag"], category: null, tags: [], caption: id, taken_at: null });
    s.result = { items: [hit("a1")], facets: {}, total: 3, next_cursor: "c1", degraded: [], possible_total: 0, possible: [] };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="貓" />
      </QueryClientProvider>,
    );
    const more = await screen.findByRole("button", { name: /載入更多/ });
    s.result = null; // pending
    const before = s.calls;
    more.click();
    (await screen.findByRole("button", { name: "載入中…" })).click(); // a second press while it loads
    asked = s.calls - before;
    expect(asked).toBe(1);
  });
});

describe("a page that came is said (audit round 105)", () => {
  it("[SRCH-UI-049] 回歸：載入更多成功時唸出「已載入 N 張，共 M 張」（驗證第 105 輪：成功沒有任何提示）", async () => {
    const hit = (id: string) => ({ asset_id: id, ch: null, score: 1, matched_by: ["tag"], category: null, tags: [], caption: id, taken_at: null });
    s.result = { items: [hit("a1"), hit("a2")], facets: {}, total: 3, next_cursor: "c1", degraded: [], possible_total: 0, possible: [] };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="貓" />
      </QueryClientProvider>,
    );
    const more = await screen.findByRole("button", { name: /載入更多/ });
    s.result = { items: [hit("a3")], facets: {}, total: 3, next_cursor: null, degraded: [], possible_total: 0, possible: [] };
    more.click();
    expect(await screen.findByText("已載入 3 張，共 3 張")).toBeTruthy();
    s.result = null;
  });
});

describe("load more keeps its place when it fails (audit round 109)", () => {
  it("[SRCH-UI-050] 回歸：載入失敗時「載入中…」原地變成「重試」（驗證第 109 輪：按鈕右移 120 px、下移 8 px）", async () => {
    const hit = (id: string) => ({ asset_id: id, ch: null, score: 1, matched_by: ["tag"], category: null, tags: [], caption: id, taken_at: null });
    s.result = { items: [hit("a1")], facets: {}, total: 3, next_cursor: "c1", degraded: [], possible_total: 0, possible: [] };
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <SearchPage q="貓" />
      </QueryClientProvider>,
    );
    const more = await screen.findByRole("button", { name: /載入更多/ });
    const row = more.parentElement;
    s.result = Promise.reject(new Error("CURSOR_INVALID")) as unknown;
    (s.result as Promise<unknown>).catch(() => undefined);
    more.click();
    const retry = await screen.findByRole("button", { name: "重試" }, { timeout: 4000 });
    expect(retry).toBe(more); // the same button, in the same place: only its words change
    expect(retry.parentElement).toBe(row);
    s.result = null;
  });
});

describe("a page that finds nothing can always browse (audit round 120)", () => {
  it("[SRCH-UI-051] 回歸：查無結果時除了問 AI，還能「瀏覽分類」到探索頁（驗證第 120 輪：拔河只剩問 AI）", async () => {
    s.result = { items: [], facets: {}, total: 0, next_cursor: null, degraded: [], possible_total: 0, possible: [] };
    const went: unknown[] = [];
    s.nav = (a: unknown) => went.push(a);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SearchPage q="拔河" />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("button", { name: "問 AI「拔河」" })).toBeTruthy();
    screen.getByRole("button", { name: "瀏覽分類" }).click();
    expect(went).toEqual([{ to: "/explore", search: { category: undefined, tag: undefined } }]);
    s.nav = null as unknown as (a: unknown) => void;
    s.result = null;
  });
});
