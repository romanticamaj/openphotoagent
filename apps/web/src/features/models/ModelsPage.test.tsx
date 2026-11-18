import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});
vi.mock("@tanstack/react-router", () => ({ Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a> }));
vi.mock("./CaptionIndex", () => ({ CaptionIndex: () => null }));
const v = (id: string, status: string | null) => ({
  id, key: id, runtime: "onnx", size_bytes: 1, size_estimated: false, vram_gb: {}, ram_gb: null,
  compat: { level: "ok", reasons: [] }, installation: status ? { id: "i" + id, status } : null, perf: { basis: "prior", prior: null },
});
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    roots: async () => [{ worker_id: "w", alias: "photos", mode: "ro" }],
    faceSettings: async () => ({ enabled: false }),
    models: async () => ({
      items: [
        { id: "qwen", display_name: "Qwen3-VL 8B", kind: "vlm", capabilities: [], license: { spdx: "Apache-2.0" }, best_variant: "q4", variants: [v("q4", "installed")] },
        { id: "siglip", display_name: "SigLIP2", kind: "embedding", capabilities: [], license: { spdx: "Apache-2.0" }, best_variant: "f16", variants: [v("f16", null)] },
      ],
    }),
  },
}));

import { ModelsPage } from "./ModelsPage";

describe("the models list", () => {
  it("[MDL-018] 回歸：類型用看得懂的名稱，看得出哪些已安裝（驗證第 1 輪：只顯示 vlm、embedding）", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ModelsPage />
      </QueryClientProvider>,
    );
    const row = (await screen.findByText("Qwen3-VL 8B")).closest("tr")!;
    expect(row.textContent).toContain("看懂照片");
    expect(row.textContent).toContain("已安裝");
    expect(screen.queryByText("vlm")).toBeNull();
    expect(screen.getByText("SigLIP2").closest("tr")!.textContent).not.toContain("已安裝");
  });
});
