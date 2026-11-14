import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});
const s = vi.hoisted(() => ({ cancel: vi.fn(), resume: vi.fn() }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    operations: async () => [
      {
        id: "op1",
        type: "job.label",
        status: "interrupted",
        subject: { kind: "job", id: "j1", job_kind: "label", folder: "第19周" },
        progress: null,
        eta: null,
        seq: 1,
        created_at: null,
        updated_at: null,
      },
    ],
    cancelOp: s.cancel,
    resumeOp: s.resume,
  },
}));

import { RecoveryCard } from "./ActivityHost";

describe("the card after a restart", () => {
  it("[ACT-009] 回歸：說是什麼、在哪個資料夾、按了會怎樣，也可以不用（驗證第 17 輪：「整理 · 已中斷」）", async () => {
    s.cancel.mockResolvedValue({});
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RecoveryCard onReview={() => {}} />
      </QueryClientProvider>,
    );
    const card = await screen.findByTestId("recovery-card");
    expect(card.textContent).toContain("AI 分析（第19周）");
    expect(card.textContent).not.toContain("整理");
    expect(card.textContent).toContain("不會動任何檔案");
    fireEvent.click(screen.getByRole("button", { name: "不用了" }));
    await waitFor(() => expect(s.cancel).toHaveBeenCalledWith("op1"));
  });
});
