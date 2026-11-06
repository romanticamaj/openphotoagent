import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const state = vi.hoisted(() => ({ ops: [] as unknown[], status: { jobs: [], current: [], recent: [] } as unknown }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: { operations: async () => state.ops, analysisStatus: async () => state.status },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}));

import { ProgressDock } from "./ProgressDock";

const op = (type: string, done?: number, total?: number, extra: Record<string, unknown> = {}) => ({
  id: type,
  type,
  status: "running",
  progress: total ? { done, total } : {},
  ...extra,
});
const ui = () => {
  const qc = new QueryClient();
  render(
    <QueryClientProvider client={qc}>
      <ProgressDock />
    </QueryClientProvider>,
  );
  return qc;
};

afterEach(() => {
  state.ops = [];
  state.status = { jobs: [], current: [], recent: [] };
});

describe("progress on every page (ADR-0029, invariant 9)", () => {
  it("[IMP-012] 沒有工作時不佔畫面", async () => {
    ui();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByTestId("progress-dock")).toBeNull();
  });

  it("[IMP-012] 分階段顯示數字與剩餘時間", async () => {
    state.ops = [op("scan", 247, 492, { eta: { state: "rough", p50_s: 90 } }), op("job.label")];
    ui();
    const dock = await screen.findByTestId("progress-dock");
    expect(dock.textContent).toContain("掃描");
    expect(dock.textContent).toContain("247");
    expect(dock.textContent).toContain("492");
    expect(dock.textContent).toContain("AI 分析");
    expect(dock.textContent).toContain("等掃描完成");
    expect(screen.getAllByRole("progressbar").length).toBe(2);
  });

  it("[IMP-012] 掃描結束、AI 還沒開始時不說在等掃描", async () => {
    state.ops = [op("job.label")];
    ui();
    const dock = await screen.findByTestId("progress-dock");
    expect(dock.textContent).toContain("準備中");
    expect(dock.textContent).not.toContain("等掃描完成");
  });

  it("[IMP-031] 同時分析多個資料夾時，合併成一條整體進度", async () => {
    state.ops = [op("job.label", 32, 80), op("job.label", 10, 20, { id: "b" }), op("job.label", undefined, undefined, { id: "c", status: "queued" })];
    ui();
    const dock = await screen.findByTestId("progress-dock");
    expect(dock.textContent).toContain("42");
    expect(dock.textContent).toContain("100");
    expect(screen.getAllByRole("progressbar").length).toBe(1);
  });

  it("[IMP-031] 看得到正在處理哪張、剛分析出什麼、每個資料夾的進度", async () => {
    state.ops = [op("job.label", 32, 80)];
    state.status = {
      jobs: [
        { job_id: "a", folder: "photos/第8周", state: "analysing", done: 32, total: 80, failed: 1 },
        { job_id: "b", folder: "photos/0410", state: "queued", done: 0, total: 0, failed: 0 },
      ],
      current: [{ asset_id: "x", ch: null, rel: "第8周/0409/IMG_1.jpg" }],
      recent: [{ asset_id: "y", ch: null, rel: "第8周/0409/IMG_0.jpg", caption: "小男孩練習足球", category: "people", category_name: "人物" }],
    };
    ui();
    const dock = await screen.findByTestId("progress-dock");
    await waitFor(() => expect(dock.textContent).toContain("IMG_1.jpg"));
    expect(dock.textContent).toContain("小男孩練習足球");
    expect(dock.textContent).toContain("人物");
    expect(dock.textContent).toContain("photos/第8周");
    expect(dock.textContent).toContain("32 / 80");
    expect(dock.textContent).toContain("排隊中");
    expect(dock.textContent).toContain("1 張失敗");
  });

  it("[IMP-012] 可以縮小成一行", async () => {
    state.ops = [op("scan", 100, 200)];
    ui();
    await screen.findByTestId("progress-dock");
    fireEvent.click(screen.getByRole("button", { name: "縮小" }));
    expect(screen.queryAllByRole("progressbar").length).toBe(0);
    expect(screen.getByTestId("progress-dock").textContent).toContain("50%");
  });

  it("[IMP-012] 完成後說明做完什麼、下一步去哪裡", async () => {
    state.ops = [op("job.label", 40, 80)];
    const qc = ui();
    await screen.findByTestId("progress-dock");
    state.ops = [];
    await act(async () => {
      await qc.refetchQueries({ queryKey: ["operations"] });
    });
    await waitFor(() => expect(screen.getByTestId("progress-dock").textContent).toContain("分析完成"));
    expect(screen.getByRole("link", { name: "看照片" }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("link", { name: "整理到資料夾" }).getAttribute("href")).toBe("/organize");
    fireEvent.click(screen.getByRole("button", { name: "關閉" }));
    expect(screen.queryByTestId("progress-dock")).toBeNull();
  });
});
