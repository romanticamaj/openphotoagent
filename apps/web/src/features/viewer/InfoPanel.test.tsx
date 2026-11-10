import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ asset: vi.fn(), nameFace: vi.fn() }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    asset: s.asset,
    preset: async () => ({ categories: [{ key: "people", name: "人物" }, { key: "other", name: "其他" }] }),
    correct: vi.fn(),
    nameFace: s.nameFace,
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

  it("[VIEW-028] 有臉但還沒有人物時說明原因", async () => {
    s.asset.mockResolvedValue({ ...base, labels: [label("r1", "people", 1, "x")], people: [], faces_found: { total: 12, small: 9 } });
    ui();
    const p = await screen.findByTestId("faces-nobody");
    expect(p.textContent).toContain("12");
    expect(p.textContent).toContain("9");
  });

  it("[VIEW-029] 臉全都太小時不說「其餘的人」", async () => {
    s.asset.mockResolvedValue({ ...base, labels: [label("r1", "people", 1, "x")], people: [], faces_found: { total: 38, small: 38 } });
    ui();
    const p = await screen.findByTestId("faces-nobody");
    expect(p.textContent).toContain("38");
    expect(p.textContent).not.toContain("其餘");
  });

  it("[VIEW-030] 回歸：信心、判斷依據與模型收在「判斷細節」裡，分類一眼看得到（驗證第 7 輪：像開發者畫面）", async () => {
    s.asset.mockResolvedValue({ ...base, labels: [label("r1", "people", 0.92, "x")] });
    ui();
    const layer = await screen.findByTestId("ai-layer");
    expect(layer.closest("details")).not.toBeNull();
    expect(screen.getByText("92%").closest("details")).not.toBeNull();
    expect(screen.getByTestId("ai-label").querySelector("details")).not.toBeNull();
    expect(screen.getByTestId("ai-category").closest("details")).toBeNull();
  });

  it("[VIEW-031] 回歸：臉的說明是一行數字，原因收在「為什麼？」裡（驗證第 9 輪：一長串灰字）", async () => {
    s.asset.mockResolvedValue({ ...base, labels: [label("r1", "people", 1, "x")], people: [], faces_found: { total: 11, small: 8 } });
    ui();
    const note = await screen.findByTestId("faces-nobody");
    expect(note.tagName).toBe("DETAILS");
    expect(note.querySelector("summary")?.textContent).toBe("11 張臉還沒分成人物 · 為什麼？");
  });

  it("[VIEW-032] 回歸：還沒分成人物的臉可以直接在這裡命名（驗證第 11 輪）", async () => {
    s.asset.mockResolvedValue({
      ...base,
      labels: [label("r1", "people", 1, "x")],
      people: [],
      faces_found: { total: 2, small: 1 },
      unknown_faces: [{ id: "fx", bbox: [0.1, 0.1, 0.2, 0.2], small: false }, { id: "fy", bbox: [0.5, 0.5, 0.02, 0.02], small: true }],
    });
    s.nameFace.mockResolvedValue({ person_id: "p9" });
    ui();
    const named = await screen.findAllByRole("button", { name: "這是誰？" });
    expect(named).toHaveLength(1); // a face too small to recognise is not offered
    fireEvent.click(named[0]);
    fireEvent.change(screen.getByLabelText("名字"), { target: { value: "阿姨" } });
    fireEvent.click(screen.getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(s.nameFace).toHaveBeenCalledWith("fx", "阿姨"));
  });

  it("[VIEW-033] 回歸：命名列一臉一列、按鈕不折行、欄位有提示，並說還有幾張太小（驗證第 12 輪）", async () => {
    s.asset.mockResolvedValue({
      ...base,
      labels: [label("r1", "people", 1, "x")],
      people: [],
      faces_found: { total: 3, small: 1 },
      unknown_faces: [
        { id: "fa", bbox: [0.1, 0.1, 0.2, 0.2], small: false },
        { id: "fb", bbox: [0.4, 0.1, 0.2, 0.2], small: false },
        { id: "fc", bbox: [0.7, 0.1, 0.02, 0.02], small: true },
      ],
    });
    const seen: string[] = [];
    window.addEventListener("opa:face-hover", (e) => seen.push((e as CustomEvent).detail ?? ""));
    ui();
    const rows = await screen.findAllByTestId("name-face-row");
    expect(rows).toHaveLength(2);
    expect(screen.getByTestId("name-faces-small").textContent).toContain("1");
    fireEvent.mouseEnter(rows[1]);
    expect(seen.at(-1)).toBe("fb");
    fireEvent.click(screen.getAllByRole("button", { name: "這是誰？" })[0]);
    expect(screen.getByLabelText("名字").getAttribute("placeholder")).toBeTruthy();
  });

  it("[VIEW-034] 回歸：「這是誰？」先列 4 張，其餘收起（驗證第 13 輪：把面板擠到下面）", async () => {
    s.asset.mockResolvedValue({
      ...base,
      labels: [label("r1", "people", 1, "x")],
      people: [],
      faces_found: { total: 7, small: 0 },
      unknown_faces: Array.from({ length: 7 }, (_, i) => ({ id: `u${i}`, bbox: [0.1 * i, 0.1, 0.05, 0.05], small: false })),
    });
    ui();
    expect(await screen.findAllByTestId("name-face-row")).toHaveLength(4);
    fireEvent.click(screen.getByRole("button", { name: "再顯示 3 張" }));
    expect(screen.getAllByTestId("name-face-row")).toHaveLength(7);
  });

  it("[VIEW-027] 資料夾沒開人臉辨識時說明，並帶去設定", async () => {
    s.asset.mockResolvedValue({ ...base, labels: [label("r1", "people", 1, "x")], faces_state: "folder_off" });
    ui();
    expect((await screen.findByTestId("faces-folder-off")).textContent).toContain("沒有開啟人臉辨識");
  });
});

