import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ detail: vi.fn(), search: vi.fn(), name: vi.fn(), cands: vi.fn(), review: vi.fn(), undoReview: vi.fn(), merge: vi.fn(), dismiss: vi.fn(), people: [] as unknown[], merges: [] as unknown[] }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: {
    people: async () => ({ people: s.people, others: 0, merge_suggestions: s.merges }),
    search: s.search,
    namePerson: s.name,
    personCandidates: s.cands,
    reviewPerson: s.review,
    undoReview: s.undoReview,
    mergePeople: s.merge,
    dismissMerge: s.dismiss,
    person: s.detail,
  },
}));
vi.mock("../viewer/Lightbox", () => ({ Lightbox: ({ start }: { start: number }) => <div role="dialog" data-start={start} /> }));
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

  it("[FACE-FIND-018] 回歸：看不清的候選標出「看不清」，分數與清晰度留在元素上；「全部都是」不包含它們（驗證第 7～8 輪，ADR-0035）", async () => {
    s.people = [row];
    s.search.mockResolvedValue({ items: [], facets: {}, total: 0, next_cursor: null });
    const c = (id: string, tier: number, clear: boolean) => ({ face_id: id, asset_id: "a" + id, ch: null, bbox: [0, 0, 0.2, 0.2], score: 0.61, tier, clear, group: null });
    s.cands.mockResolvedValue({ items: [c("f1", 1, true), c("f2", 3, false)], references: 4 });
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /未命名/ }));
    const tiles = await screen.findAllByTestId("candidate");
    expect(tiles[0].dataset.tier).toBe("1");
    expect(tiles[1].dataset.score).toBe("0.61");
    expect(tiles[0].querySelector(".candidate-hard")).toBeNull(); // a profile is not blurry
    expect(tiles[1].querySelector(".candidate-hard")?.textContent).toBe("看不清");
    fireEvent.click(screen.getByRole("button", { name: "全部都是" }));
    expect(tiles[0].getAttribute("data-mark")).toBe("yes");
    expect(tiles[1].getAttribute("data-mark")).toBe("none");
  });

  it("[FACE-FIND-020] 回歸：「全部都是」只標把握高的，其餘一張一張決定（驗證第 9 輪）", async () => {
    s.people = [row];
    s.search.mockResolvedValue({ items: [], facets: {}, total: 0, next_cursor: null });
    const c = (id: string, score: number, sure: boolean) => ({ face_id: id, asset_id: "a" + id, ch: null, bbox: [0, 0, 0.2, 0.2], score, tier: 2, clear: true, sure, group: null });
    s.cands.mockResolvedValue({ items: [c("f1", 0.7, true), c("f2", 0.583, false)], references: 4 });
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /未命名/ }));
    const tiles = await screen.findAllByTestId("candidate");
    fireEvent.click(screen.getByRole("button", { name: "全部都是" }));
    expect(tiles[0].getAttribute("data-mark")).toBe("yes");
    expect(tiles[1].getAttribute("data-mark")).toBe("none");
    expect(screen.getByTestId("all-yes-hint").textContent).toContain("1");
  });

  it("[FACE-UI-019] 回歸：用鍵盤一張一張決定：Y 是、N 不是、S 略過，自動跳到下一張（驗證第 11 輪）", async () => {
    s.people = [row];
    s.search.mockResolvedValue({ items: [], facets: {}, total: 0, next_cursor: null });
    const c = (id: string) => ({ face_id: id, asset_id: "a" + id, ch: null, bbox: [0, 0, 0.2, 0.2], score: 0.55, tier: 2, clear: true, sure: false, group: null });
    s.cands.mockResolvedValue({ items: [c("f1"), c("f2"), c("f3"), c("f4")], references: 4 });
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /未命名/ }));
    const tiles = await screen.findAllByTestId("candidate");
    expect(tiles[0].getAttribute("data-current")).toBe("true");
    fireEvent.keyDown(window, { key: "y" });
    fireEvent.keyDown(window, { key: "n" });
    fireEvent.keyDown(window, { key: "s" });
    fireEvent.keyDown(window, { key: "y" });
    expect(tiles.map((x) => x.getAttribute("data-mark"))).toEqual(["yes", "no", "none", "yes"]);
    expect(screen.getByTestId("candidate-keys").textContent).toContain("Y");
  });

  it("[FACE-UI-020] 回歸：點開一個人，列出他所有的照片：確定的加上可能的，可能的標出來（使用者回報）", async () => {
    s.people = [row];
    s.search.mockResolvedValue({ items: [{ asset_id: "b1", ch: null, caption: "在操場", category: "people", tags: [], taken_at: null, score: 1, matched_by: [] }], facets: {}, total: 1, next_cursor: null });
    const c = (id: string, asset: string) => ({ face_id: id, asset_id: asset, ch: null, bbox: [0, 0, 0.2, 0.2], score: 0.62, tier: 2, clear: true, sure: true, group: null });
    s.cands.mockResolvedValue({ items: [c("f1", "c1"), c("f2", "c2"), c("f3", "b1")], references: 4 });
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /未命名/ }));
    await waitFor(() => expect(screen.getAllByTestId("person-photo")).toHaveLength(3)); // b1 once, plus c1 and c2
    expect(screen.getAllByTestId("person-photo").filter((x) => x.dataset.maybe === "true")).toHaveLength(2);
    expect(screen.getByTestId("person-photos-title").textContent).toBe("照片 3 張（1 張確定、2 張可能）");
  });

  it("[FACE-UI-021] 回歸：照片牆的「可能」只放把握高的（驗證第 13 輪：另一個孩子 0.583 出現在可能裡）", async () => {
    s.people = [row];
    s.search.mockResolvedValue({ items: [], facets: {}, total: 0, next_cursor: null });
    const c = (id: string, asset: string, score: number, sure: boolean) => ({ face_id: id, asset_id: asset, ch: null, bbox: [0, 0, 0.2, 0.2], score, tier: 2, clear: true, sure, group: null });
    s.cands.mockResolvedValue({ items: [c("f1", "c1", 0.7, true), c("f2", "c2", 0.583, false)], references: 4 });
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /未命名/ }));
    await waitFor(() => expect(screen.getAllByTestId("person-photo")).toHaveLength(1));
  });

  it("[FACE-UI-022] 回歸：已命名的人，標題是名字，改名要按「改名」（驗證第 13 輪）", async () => {
    s.people = [{ ...row, name: "測試A", status: "named" }];
    s.search.mockResolvedValue({ items: [], facets: {}, total: 0, next_cursor: null });
    s.cands.mockResolvedValue({ items: [], references: 0 });
    s.name.mockResolvedValue({});
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /測試A/ }));
    expect((await screen.findByRole("heading", { name: "測試A" })).tagName).toBe("H2");
    expect(screen.queryByLabelText("這個人是誰？")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "改名" }));
    fireEvent.change(screen.getByLabelText("名字"), { target: { value: "測試C" } });
    fireEvent.click(screen.getByRole("button", { name: "儲存名字" }));
    await waitFor(() => expect(s.name).toHaveBeenCalledWith("p1", "測試C"));
  });

  it("[FACE-FIND-028] 回歸：分數低的候選標「不太像」（驗證第 14 輪）", async () => {
    s.people = [row];
    s.search.mockResolvedValue({ items: [], facets: {}, total: 0, next_cursor: null });
    const c = (id: string, score: number, faint: boolean) => ({ face_id: id, asset_id: "a" + id, ch: null, bbox: [0, 0, 0.2, 0.2], score, tier: 2, clear: true, sure: score >= 0.6, faint, group: null });
    s.cands.mockResolvedValue({ items: [c("f1", 0.62, false), c("f2", 0.504, true)], references: 4 });
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /未命名/ }));
    const tiles = await screen.findAllByTestId("candidate");
    expect(tiles[0].querySelector(".candidate-faint")).toBeNull();
    expect(tiles[1].querySelector(".candidate-faint")?.textContent).toBe("不太像");
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

  it("[FACE-UI-014] 回歸：先看到這個人的照片，點了打開；候選一次 12 張，「全部都是」只管眼前這些（驗證第 2 輪）", async () => {
    s.people = [row];
    s.search.mockResolvedValue({ items: [{ asset_id: "b1", ch: null, caption: "在操場", category: "people", tags: [], taken_at: null, score: 1, matched_by: [] }], facets: {}, total: 1, next_cursor: null });
    const c = (i: number) => ({ face_id: `f${i}`, asset_id: `a${i}`, ch: null, bbox: [0.4, 0.3, 0.1, 0.1], score: 0.6, group: null });
    s.cands.mockResolvedValue({ items: Array.from({ length: 30 }, (_, i) => c(i)), references: 4 });
    s.review.mockResolvedValue({ confirmed: 12, rejected: 0 });
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /未命名/ }));
    const photo = await screen.findByRole("button", { name: /在操場/ });
    const firstCand = (await screen.findAllByTestId("candidate"))[0];
    expect(photo.compareDocumentPosition(firstCand) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); // photos first
    fireEvent.click(photo);
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(screen.getAllByTestId("candidate")).toHaveLength(12);
    fireEvent.click(screen.getByRole("button", { name: "全部都是" }));
    fireEvent.click(screen.getByRole("button", { name: /送出/ }));
    await waitFor(() => expect(s.review).toHaveBeenCalled());
    expect(s.review.mock.calls.at(-1)![1].yes).toHaveLength(12);
    fireEvent.click(screen.getByRole("button", { name: /再看/ }));
    expect(screen.getAllByTestId("candidate")).toHaveLength(24);
  });

  it("[FACE-FIND-016] 回歸：送出後可以復原（驗證第 5 輪）", async () => {
    s.people = [row];
    s.search.mockResolvedValue({ items: [], facets: {}, total: 0, next_cursor: null });
    s.cands.mockResolvedValue({ items: [{ face_id: "f9", asset_id: "a9", ch: null, bbox: [0, 0, 0.2, 0.2], score: 0.7, group: null }], references: 1 });
    s.review.mockResolvedValue({ confirmed: 1, rejected: 0 });
    s.undoReview.mockResolvedValue({ restored: 1 });
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /未命名/ }));
    await screen.findByTestId("candidate");
    fireEvent.click(screen.getByRole("button", { name: "全部都是" }));
    fireEvent.click(screen.getByRole("button", { name: /送出/ }));
    expect((await screen.findByTestId("review-done")).textContent).toContain("1");
    fireEvent.click(screen.getByRole("button", { name: "復原" }));
    await waitFor(() => expect(s.undoReview).toHaveBeenCalledWith("p1"));
    await waitFor(() => expect(screen.queryByTestId("review-done")).toBeNull());
  });

  it("[FACE-MGT-010] 「可能是同一人？」：兩張臉並排，是就合併，不是就不再問", async () => {
    const p2 = { ...row, id: "p2", cover: { asset_id: "a2", ch: null, bbox: [0, 0, 0.2, 0.2] } };
    s.people = [{ ...row, cover: { asset_id: "a1", ch: null, bbox: [0, 0, 0.2, 0.2] } }, p2];
    s.merges = [{ a: "p1", b: "p2", score: 0.9 }];
    s.merge.mockResolvedValue({ undo_token: "t", moved: 3 });
    s.dismiss.mockResolvedValue({});
    ui();
    const card = await screen.findByTestId("merge-suggestion");
    expect(card.querySelectorAll(".face-crop")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "是同一人" }));
    await waitFor(() => expect(s.merge).toHaveBeenCalledWith("p2", "p1"));
    s.merges = [{ a: "p1", b: "p2", score: 0.9 }];
    fireEvent.click(await screen.findByRole("button", { name: "不是同一人" }));
    await waitFor(() => expect(s.dismiss).toHaveBeenCalledWith("p1", "p2"));
  });

  it("[FACE-MGT-013] 回歸：合併前看得到兩組各自的幾張臉（驗證第 5 輪）", async () => {
    const fc = (p: string, i: number) => ({ id: `${p}f${i}`, asset_id: `${p}a${i}`, ch: null, state: "auto", tier: 2, bbox: [0, 0, 0.2, 0.2] });
    s.detail.mockImplementation(async (id: string) => ({ id, faces: Array.from({ length: 9 }, (_, i) => fc(id, i)) }));
    s.people = [{ ...row, cover: { asset_id: "a1", ch: null, bbox: [0, 0, 0.2, 0.2] } }, { ...row, id: "p2", cover: { asset_id: "a2", ch: null, bbox: [0, 0, 0.2, 0.2] } }];
    s.merges = [{ a: "p1", b: "p2", score: 0.9 }];
    ui();
    const card = await screen.findByTestId("merge-suggestion");
    await waitFor(() => expect(card.querySelectorAll("[data-testid=merge-more] .face-crop")).toHaveLength(10)); // 5 a side
    expect(s.detail).toHaveBeenCalledWith("p1");
    expect(s.detail).toHaveBeenCalledWith("p2");
  });

  it("[FACE-MGT-015] 回歸：合併卡片的兩排臉各自標出是哪一組（驗證第 6 輪）", async () => {
    const fc = (p: string, i: number) => ({ id: `${p}f${i}`, asset_id: `${p}a${i}`, ch: null, state: "auto", tier: 2, bbox: [0, 0, 0.2, 0.2] });
    s.detail.mockImplementation(async (id: string) => ({ id, faces: Array.from({ length: 3 }, (_, i) => fc(id, i)) }));
    s.people = [
      { ...row, name: "小明", asset_count: 9, cover: { asset_id: "a1", ch: null, bbox: [0, 0, 0.2, 0.2] } },
      { ...row, id: "p2", asset_count: 4, cover: { asset_id: "a2", ch: null, bbox: [0, 0, 0.2, 0.2] } },
    ];
    s.merges = [{ a: "p1", b: "p2", score: 0.95 }];
    ui();
    await waitFor(() => expect(screen.getAllByTestId("merge-row-label")).toHaveLength(2));
    const labels = screen.getAllByTestId("merge-row-label").map((x) => x.textContent ?? "");
    expect(labels[0]).toContain("小明");
    expect(labels[0]).toContain("9");
    expect(labels[1]).toContain("4");
  });

  it("[FACE-MGT-011] 卡片挑兩組都在牆上的那一對", async () => {
    s.people = [{ ...row, cover: { asset_id: "a1", ch: null, bbox: [0, 0, 0.2, 0.2] } }, { ...row, id: "p2", cover: { asset_id: "a2", ch: null, bbox: [0, 0, 0.2, 0.2] } }];
    s.merges = [{ a: "p1", b: "folded", score: 0.95 }, { a: "p1", b: "p2", score: 0.8 }];
    ui();
    const card = await screen.findByTestId("merge-suggestion");
    expect(card.querySelectorAll(".face-crop")).toHaveLength(2);
  });

  it("[FACE-MGT-012] 「稍後再說」換下一對，不改任何東西", async () => {
    const face = (id: string) => ({ ...row, id, cover: { asset_id: `a${id}`, ch: null, bbox: [0, 0, 0.2, 0.2] } });
    s.people = [face("p1"), face("p2"), face("p3")];
    s.merges = [{ a: "p1", b: "p2", score: 0.9 }, { a: "p1", b: "p3", score: 0.8 }];
    s.merge.mockReset();
    s.dismiss.mockReset();
    ui();
    const first = await screen.findByTestId("merge-suggestion");
    expect(first.querySelectorAll("img")[1].getAttribute("src")).toContain("ap2");
    fireEvent.click(screen.getByRole("button", { name: "稍後再說" }));
    await waitFor(() => expect(screen.getByTestId("merge-suggestion").querySelectorAll("img")[1].getAttribute("src")).toContain("ap3"));
    expect(s.merge).not.toHaveBeenCalled();
    expect(s.dismiss).not.toHaveBeenCalled();
  });
});

