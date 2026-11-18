import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    captionIndex: async () => ({ active: null, building: { model_key: "m", total: 10, done: 4, ready: false, operation_id: "op1" } }),
    operations: async () => [
      { id: "op1", type: "index.caption", status: "running", progress: { done: 4, total: 10 }, eta: { state: "rough", p50_s: 30 }, started_at: new Date().toISOString() },
    ],
  },
}));

import { CaptionIndex } from "./CaptionIndex";

describe("caption index (audit CI run 43028504956)", () => {
  it("[SRCH-R2-012] 回歸：建立中的索引只有一條進度列（有 Operation 時就用它，含剩餘時間）", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <CaptionIndex />
      </QueryClientProvider>,
    );
    await screen.findByText(/正在建立/);
    await waitFor(() => expect(screen.getAllByRole("progressbar")).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getAllByRole("progressbar")).toHaveLength(1);
  });
});
