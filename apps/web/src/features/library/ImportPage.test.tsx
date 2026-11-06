import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ nav: vi.fn(), analyze: vi.fn(), scan: vi.fn() }));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  useNavigate: () => s.nav,
}));
vi.mock("../../components/FolderPicker", () => ({
  FolderPicker: ({ onPick, action }: { onPick: (r: string, p: string) => void; action: string }) => <button onClick={() => onPick("photos", "trip")}>{action}</button>,
}));
vi.mock("../../lib/api", async (orig) => {
  const real = await orig<typeof import("../../lib/api")>();
  return {
    ...real,
    api: { createSource: async () => ({ id: "src1" }), analyzeSource: s.analyze, scanSource: s.scan },
  };
});

import { ApiError } from "../../lib/api";
import { ImportPage } from "./ImportPage";

const ui = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ImportPage />
    </QueryClientProvider>,
  );

beforeEach(() => {
  s.nav.mockReset();
  s.analyze.mockReset();
  s.scan.mockReset();
});

describe("匯入 (ADR-0029)", () => {
  it("[IMP-011] 加入資料夾就開始分析，並回到照片頁", async () => {
    s.analyze.mockResolvedValue({ job_id: "j1" });
    ui();
    fireEvent.click(await screen.findByRole("button", { name: "加入圖庫並開始分析" }));
    await waitFor(() => expect(s.nav).toHaveBeenCalledWith({ to: "/" }));
    expect(s.analyze).toHaveBeenCalledWith("src1");
    expect(s.scan).not.toHaveBeenCalled();
  });

  it("[IMP-011] 模型還沒好時說明原因，可以先只加入照片", async () => {
    s.analyze.mockRejectedValue(new ApiError(409, { blockers: [{ code: "MODEL_NOT_INSTALLED", params: {}, fixes: [] }] }));
    s.scan.mockResolvedValue({ task_id: "t" });
    ui();
    fireEvent.click(await screen.findByRole("button", { name: "加入圖庫並開始分析" }));
    const only = await screen.findByRole("button", { name: "先只加入照片，不做 AI 分析" });
    expect(document.querySelector('[data-code="MODEL_NOT_INSTALLED"]')).not.toBeNull();
    expect(s.nav).not.toHaveBeenCalled();
    fireEvent.click(only);
    await waitFor(() => expect(s.nav).toHaveBeenCalledWith({ to: "/" }));
    expect(s.scan).toHaveBeenCalledWith("src1");
  });
});
