import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ search: vi.fn(), name: vi.fn(), cands: vi.fn(), review: vi.fn(), people: [] as unknown[] }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: { people: async () => ({ people: s.people, others: 0 }), search: s.search, namePerson: s.name, personCandidates: s.cands, reviewPerson: s.review },
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
  beforeEach(() => {
    s.cands.mockResolvedValue({ items: [], references: 0 });
  });

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

  it("[FACE-UI-011] 可能也是他：點一下是、再點一下不是，送出後再找", async () => {
    s.people = [row];
    s.search.mockResolvedValue({ items: [], facets: {}, total: 0, next_cursor: null });
    const c = (id: string) => ({ face_id: id, asset_id: "a" + id, ch: null, bbox: [0.4, 0.3, 0.1, 0.1], score: 0.6, group: null });
    s.cands.mockResolvedValue({ items: [c("f1"), c("f2"), c("f3")], references: 4 });
    s.review.mockResolvedValue({ confirmed: 1, rejected: 1 });
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /未命名/ }));
    const tiles = await screen.findAllByTestId("candidate");
    expect(tiles).toHaveLength(3);
    fireEvent.click(tiles[0]);
    fireEvent.click(tiles[1]);
    fireEvent.click(tiles[1]);
    expect(tiles[0].getAttribute("data-mark")).toBe("yes");
    expect(tiles[1].getAttribute("data-mark")).toBe("no");
    expect(tiles[2].getAttribute("data-mark")).toBe("none");
    fireEvent.click(screen.getByRole("button", { name: /送出/ }));
    await waitFor(() => expect(s.review).toHaveBeenCalledWith("p1", { yes: ["f1"], no: ["f2"] }));
    await waitFor(() => expect(s.cands.mock.calls.length).toBeGreaterThan(1)); // the search widens with what was confirmed
  });

  it("[FACE-UI-011] 一鍵把全部標成是", async () => {
    s.people = [row];
    s.search.mockResolvedValue({ items: [], facets: {}, total: 0, next_cursor: null });
    s.cands.mockResolvedValue({ items: [{ face_id: "f9", asset_id: "a9", ch: null, bbox: [0, 0, 0.2, 0.2], score: 0.7, group: null }], references: 1 });
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /未命名/ }));
    await screen.findByTestId("candidate");
    fireEvent.click(screen.getByRole("button", { name: "全部都是" }));
    expect(screen.getByTestId("candidate").getAttribute("data-mark")).toBe("yes");
  });
});
