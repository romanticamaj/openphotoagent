import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const loc = vi.hoisted(() => ({ value: { pathname: "/search", search: { q: "大象" } as Record<string, unknown> } }));
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn(), useLocation: () => loc.value }));
vi.mock("../../lib/api", () => ({ api: { preflight: async () => ({ blockers: [], warnings: [], ready: true }) } }));

import { SearchBar } from "./SearchBar";

const ui = () => (
  <QueryClientProvider client={new QueryClient()}>
    <SearchBar />
  </QueryClientProvider>
);

describe("the top search box", () => {
  it("[SRCH-UI-010] 回歸：顯示目前的搜尋；離開搜尋頁就清空（驗證第 1 輪：換頁後還留著「大象」）", () => {
    const { rerender } = render(ui());
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("大象");
    loc.value = { pathname: "/people", search: {} };
    rerender(ui());
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("");
  });
});
