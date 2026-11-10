import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});
const s = vi.hoisted(() => ({ job: vi.fn() }));
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: {
    reviews: async () => ({ count: 1, items: [{ id: "r1", asset_id: "a1", reason: "low_confidence", candidates: ["people", "art"], suggested: "people" }] }),
    preset: async () => ({ categories: [{ key: "people", name: "人物" }, { key: "art", name: "藝術" }] }),
    job: s.job,
  },
}));

import { ReviewPage } from "./ReviewPage";

describe("the review queue", () => {
  it("[REV-010] 回歸：從導覽進來（沒有 job）不打 GET /jobs/；「0 其他」不重複寫 0（驗證第 1 輪）", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ReviewPage jobId="" />
      </QueryClientProvider>,
    );
    const other = await screen.findByRole("button", { name: /其他/ });
    expect(other.textContent?.replace(/\s+/g, "")).toBe("0其他");
    expect(s.job).not.toHaveBeenCalled();
  });

  it("[REV-011] 回歸：每張照片說明為什麼要你確認（驗證第 5 輪）", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ReviewPage jobId="" />
      </QueryClientProvider>,
    );
    expect((await screen.findByTestId("review-reason")).textContent).toContain("把握不夠");
  });
});
