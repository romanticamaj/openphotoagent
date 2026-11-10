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

  it("[VIEW-024] 回歸：同一個檔案只列一次（驗證第 2 輪：巢狀資料夾讓路徑印兩次）", async () => {
    const loc = (sid: string) => ({ source_id: sid, root_alias: "testdata", rel_path: "第8周/0409/a.jpg", missing: false });
    s.asset.mockResolvedValue({ ...base, labels: [label("r1", "people", 1, "說明")], locations: [loc("s3"), loc("s4")] });
    ui();
    await screen.findByText("說明");
    expect(screen.getAllByText("testdata/第8周/0409/a.jpg")).toHaveLength(1);
  });

  it("[VIEW-025] 照片中的人物附上這張照片裡的臉", async () => {
    s.asset.mockResolvedValue({ ...base, labels: [label("r1", "people", 1, "x")], people: [{ id: "p1", name: null }], people_faces: { p1: { bbox: [0.1, 0.1, 0.2, 0.2], ch: "h" } } });
    ui();
    const list = await screen.findByTestId("photo-people");
    expect(list.querySelector(".face-crop")).toBeTruthy();
  });

  it("[VIEW-027] 資料夾沒開人臉辨識時說明，並帶去設定", async () => {
    s.asset.mockResolvedValue({ ...base, labels: [label("r1", "people", 1, "x")], faces_state: "folder_off" });
    ui();
    expect((await screen.findByTestId("faces-folder-off")).textContent).toContain("沒有開啟人臉辨識");
  });
});

