import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ nav: vi.fn(), trash: vi.fn() }));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  useNavigate: () => s.nav,
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));
vi.mock("../lib/api", async (orig) => {
  const real = await orig<typeof import("../lib/api")>();
  return { ...real, api: { selectionTrash: s.trash } };
});

import { ApiError } from "../lib/api";
import { TrashButton } from "./TrashButton";

const wrap = (el: React.ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>);

describe("移到垃圾桶 (approved 2026-11-12)", () => {
  it("[TRASH-013] 按「移到垃圾桶」建立計畫並打開計畫頁；唯讀資料夾說出原因", async () => {
    s.trash.mockResolvedValueOnce({ plan_id: "p9", job_id: "j9", trash: 2 });
    const select = vi.fn().mockResolvedValue({ id: "sel1" });
    const { unmount } = wrap(<TrashButton select={select} />);
    fireEvent.click(screen.getByRole("button", { name: "移到垃圾桶" }));
    await waitFor(() => expect(s.trash).toHaveBeenCalledWith("sel1"));
    await waitFor(() => expect(s.nav).toHaveBeenCalledWith({ to: "/plans/$planId", params: { planId: "p9" } }));
    unmount();
    s.trash.mockRejectedValueOnce(new ApiError(409, { detail: { code: "FS_NOT_WRITABLE", roots: ["photos"] } }));
    wrap(<TrashButton select={select} />);
    fireEvent.click(screen.getByRole("button", { name: "移到垃圾桶" }));
    expect((await screen.findByRole("alert")).textContent).toContain("無法寫入「photos」");
  });
});
