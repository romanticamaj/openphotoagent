import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ nav: vi.fn(), trash: vi.fn(), roots: [{ alias: "photos", mode: "rw" }] as Array<{ alias: string; mode: string }> }));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  useNavigate: () => s.nav,
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}));
vi.mock("../lib/api", async (orig) => {
  const real = await orig<typeof import("../lib/api")>();
  return { ...real, api: { selectionTrash: s.trash, roots: async () => s.roots } };
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
    expect((await screen.findByRole("alert")).textContent).toContain("「photos」是唯讀資料夾"); // says why, not only 「無法寫入」 (TRASH-015)
  });

  it("[TRASH-015] 回歸：被拒時說資料夾是唯讀、什麼都沒變、怎麼做；浮在按鈕旁可關閉（驗證第 61 輪：只說「無法寫入」）", async () => {
    s.roots = [{ alias: "photos", mode: "ro" }, { alias: "sorted", mode: "rw" }];
    s.trash.mockRejectedValueOnce(new ApiError(409, { code: "FS_NOT_WRITABLE", roots: ["photos"] }));
    wrap(<TrashButton select={vi.fn().mockResolvedValue({ id: "sel1" })} />);
    fireEvent.click(await screen.findByRole("button", { name: "移到垃圾桶" }));
    const card = await screen.findByRole("alert");
    expect(card.textContent).toContain("「photos」是唯讀資料夾");
    expect(card.textContent).toContain("沒有任何檔案被移動");
    expect(screen.getByRole("link", { name: /以可寫入的方式重新加入/ }).getAttribute("href")).toBe("/import");
    expect(card.closest(".trash-popover")).not.toBeNull(); // floats beside the button: the bar keeps its height
    fireEvent.click(screen.getByRole("button", { name: "關閉" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("[TRASH-016] 回歸：所有資料夾都是唯讀時，按鈕先說原因（驗證第 61 輪：按了才被拒）", async () => {
    s.roots = [{ alias: "photos", mode: "ro" }];
    wrap(<TrashButton select={vi.fn()} />);
    const b = await screen.findByRole("button", { name: "移到垃圾桶" });
    await waitFor(() => expect(b.getAttribute("aria-disabled")).toBe("true"));
    const why = document.getElementById(b.getAttribute("aria-describedby") ?? "");
    expect(why?.textContent).toContain("唯讀");
  });
});

