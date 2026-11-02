import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import "../../lib/i18n";

vi.mock("@tanstack/react-router", () => ({ Link: (p: { children: unknown }) => p.children }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    roots: async () => [{ worker_id: "local", alias: "photos", mode: "ro" }],
    recommendation: async () => ({
      mode: "standard",
      hardware: { os: "windows", gpu: "RTX 5070 Ti", vram_gb: 16, ram_gb: 32 },
      combo: { variants: ["qwen3-vl-8b-instruct@q4_k_m", "siglip2@fp16"], packs: ["cuda12"], download_bytes: 6e9, download_minutes: 9, estimated: true },
      per_1000_minutes: null,
      licenses: ["Apache-2.0"],
      warnings: [],
    }),
    model: () => new Promise(() => {}), // the model card never answers in this test
  },
}));

import { Welcome } from "./Welcome";

describe("onboarding plan card", () => {
  it("[MDL-013] 回歸：模型資料還沒回來時不顯示「未檢查」徽章（MDL-005 偶發失敗）", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Welcome />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /開始|get started/i }));
    await screen.findByTestId("plan-card");
    expect(screen.queryByTestId("compat-badge")).toBeNull();
    expect(screen.getByTestId("compat-loading")).toBeTruthy();
  });
});
