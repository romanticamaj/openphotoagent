import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ discarded: false, discard: vi.fn(), approve: vi.fn() }));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  // forwards its ref, as the router's Link does
  Link: (await import("react")).forwardRef<HTMLAnchorElement, { to: string; children: React.ReactNode }>(({ to, children }, ref) => (
    <a ref={ref} href={to}>
      {children}
    </a>
  )),
  useNavigate: () => vi.fn(),
}));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    plan: async () => ({
      id: "p1",
      job_id: "j1",
      status: "draft",
      discarded: s.discarded,
      summary: { copy: 2, folders: 1 },
      outcome: null,
      actions: [
        { id: "a1", seq: 0, asset_id: "x", action: "copy", src_alias: "photos", src_rel: "a.jpg", dst_alias: "sorted", dst_rel: "2024/a.jpg" },
        { id: "a2", seq: 1, asset_id: "y", action: "copy", src_alias: "photos", src_rel: "b.jpg", dst_alias: "sorted", dst_rel: "2024/b.jpg" },
      ],
    }),
    discardPlan: async (id: string) => {
      s.discard(id);
      s.discarded = true;
      return { id, discarded: true };
    },
    approve: s.approve,
  },
}));

import { PlanPage } from "./PlanPage";

describe("a plan you do not want (ADR-0045)", () => {
  it("[ORG-020] 回歸：計畫頁可以「捨棄計畫」，之後說原檔沒有變更、不能再核准（驗證第 57 輪：只有核准）", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <PlanPage planId="p1" />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "捨棄計畫" }));
    await waitFor(() => expect(s.discard).toHaveBeenCalledWith("p1"));
    expect((await screen.findByTestId("plan-discarded")).textContent).toContain("已捨棄計畫，原檔沒有任何變更");
    expect(screen.queryByRole("button", { name: "核准並開始" })).toBeNull();
    expect(screen.getByRole("link", { name: "回到整理" }).getAttribute("href")).toBe("/organize");
    expect(screen.getByTestId("plan-status").textContent).toBe("已捨棄"); // not 「待核准」 (audit round 58)
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("link", { name: "回到整理" })));
    expect(s.approve).not.toHaveBeenCalled();
  });

  it("[ORG-021] 回歸：計畫的目的地寫出資料夾名稱，不是「.」（驗證第 64 輪：「. (2)」）", async () => {
    s.discarded = false;
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <PlanPage planId="p1" />
      </QueryClientProvider>,
    );
    const tree = await screen.findByTestId("plan-tree");
    const dirs = [...tree.querySelectorAll("summary")].map((x) => x.textContent?.replace(/\s*\(\d+\)$/, ""));
    expect(dirs).toEqual(["sorted/2024"]);
    expect(tree.textContent).not.toMatch(/^\.|\s\.\s/);
  });
});

