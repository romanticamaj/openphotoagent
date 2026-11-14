import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ plan: vi.fn() }));
vi.mock("@tanstack/react-router", async (orig) => ({ ...(await orig<typeof import("@tanstack/react-router")>()), useNavigate: () => vi.fn() }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: {
    duplicates: async () => ({
      groups: [{ group_id: "g1", reason: "near", members: [{ asset_id: "a", ch: null, rel_path: "x/a.jpg" }, { asset_id: "b", ch: null, rel_path: "x/b.jpg" }], representative: { asset_id: "a" }, reclaimable_bytes: 10 }],
    }),
    roots: async () => [{ alias: "photos", mode: "ro" }, { alias: "sorted", mode: "rw" }],
    sources: async () => [{ id: "s1", root_alias: "photos", sub_path: "" }],
    cleanupPlan: s.plan,
  },
}));

import { DuplicatesPage } from "./DuplicatesPage";

describe("clean-up on a read-only library (audit rounds 63-64)", () => {
  it("[SRCH-DUP-010] 回歸：圖庫資料夾都是唯讀時，「產生清理計畫」先說不能移到垃圾桶（驗證第 64 輪：仍承諾移到垃圾桶）", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <DuplicatesPage />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("checkbox", { name: "全選" }));
    const b = await screen.findByRole("button", { name: /產生清理計畫/ });
    await waitFor(() => expect(b.getAttribute("aria-disabled")).toBe("true"));
    expect(document.getElementById(b.getAttribute("aria-describedby") ?? "")?.textContent).toContain("唯讀");
    fireEvent.click(b);
    expect(s.plan).not.toHaveBeenCalled();
    // the page says the same thing everywhere: no promise of the trash, no space to free (audit round 65)
    expect(screen.getByTestId("dup-intro").textContent).toContain("唯讀");
    expect(document.body.textContent).not.toContain("可釋放");
  });
});
