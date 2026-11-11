import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    search: () => new Promise(() => undefined), // still searching
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
