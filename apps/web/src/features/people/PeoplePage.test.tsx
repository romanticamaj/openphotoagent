import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ search: vi.fn(), name: vi.fn(), people: [] as unknown[] }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: { people: async () => ({ people: s.people, others: 0 }), search: s.search, namePerson: s.name },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}));

import { PeoplePage } from "./PeoplePage";

const row = { id: "p1", name: null, status: "unnamed", face_count: 12, asset_count: 9, cover: { asset_id: "a1", ch: null } };
const ui = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PeoplePage />
    </QueryClientProvider>,
  );

describe("finding a person (07 §4)", () => {
  it("[FACE-UI-010] 回歸：人物卡片有照片，點開看到這個人的照片", async () => {
    s.people = [row];
    s.search.mockResolvedValue({ items: [{ asset_id: "b1", ch: null, caption: "在操場", category: "people", tags: [], taken_at: null, score: 1, matched_by: [] }], facets: {}, total: 1, next_cursor: null });
    ui();
    const card = await screen.findByTestId("person");
    expect(card.querySelector("img")?.getAttribute("src")).toBe("/m/a1");
    fireEvent.click(screen.getByRole("button", { name: /未命名/ }));
    await waitFor(() => expect(s.search).toHaveBeenCalled());
    expect(s.search.mock.calls[0][0].filters.persons_any).toEqual(["p1"]);
    expect(await screen.findByText("在操場")).toBeTruthy();
  });

  it("[FACE-UI-010] 可以幫人物命名", async () => {
    s.people = [row];
    s.search.mockResolvedValue({ items: [], facets: {}, total: 0, next_cursor: null });
    s.name.mockResolvedValue({});
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /未命名/ }));
    fireEvent.change(await screen.findByLabelText("這個人是誰？"), { target: { value: "小明" } });
    fireEvent.click(screen.getByRole("button", { name: "儲存名字" }));
    await waitFor(() => expect(s.name).toHaveBeenCalledWith("p1", "小明"));
  });

  it("[FACE-UI-010] 還沒有人物時說明原因", async () => {
    s.people = [];
    ui();
    await screen.findByText(/正在把相同的臉分在一起/);
  });
});
