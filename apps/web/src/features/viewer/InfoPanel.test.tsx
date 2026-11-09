import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ asset: vi.fn() }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    asset: s.asset,
    preset: async () => ({ categories: [{ key: "people", name: "人物" }, { key: "other", name: "其他" }] }),
    correct: vi.fn(),
  },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));

import { InfoPanel } from "./Lightbox";

const label = (id: string, category: string, confidence: number, caption: string) => ({
  result_id: id,
  task_type: "label",
  category,
  confidence,
  decision: "accepted",
  source: "consensus",
  model_fingerprint: "m",
  caption,
  tags: [],
  tag_names: {},
});
const base = { id: "a1", width: 4000, height: 3000, taken_at: null, date_source: null, size_bytes: 1, exif: {}, human_label: null, locations: [] };
const ui = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <InfoPanel id="a1" onSaved={() => {}} />
    </QueryClientProvider>,
  );

describe("the info panel", () => {
  it("[VIEW-020] 回歸：只列目前的分析，舊的收起來（驗證第 1 輪：同一張列出 3 個互相矛盾的結果）", async () => {
    s.asset.mockResolvedValue({ ...base, labels: [label("r3", "people", 1, "新的說明"), label("r2", "other", 0.22, "舊的"), label("r1", "people", 0.92, "更舊")] });
    ui();
    await screen.findByText("新的說明");
    const current = screen.getAllByTestId("ai-label");
    expect(current).toHaveLength(1);
    expect(current[0].textContent).toContain("人物");
    const history = screen.getByTestId("ai-history");
    expect(history.tagName).toBe("DETAILS");
    expect(history.hasAttribute("open")).toBe(false);
    expect(history.textContent).toContain("2");
  });

  it("[VIEW-021] 照片裡有誰：列出人物，點了可以找這個人", async () => {
    s.asset.mockResolvedValue({ ...base, labels: [label("r1", "people", 1, "x")], people: [{ id: "p1", name: "小明" }, { id: "p2", name: null }] });
    ui();
    const list = await screen.findByTestId("photo-people");
    expect(list.textContent).toContain("小明");
    expect(list.textContent).toContain("未命名");
  });
});
