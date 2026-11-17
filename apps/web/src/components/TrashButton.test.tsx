import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ nav: vi.fn(), trash: vi.fn(), roots: [{ alias: "photos", mode: "rw" }] as Array<{ alias: string; mode: string }>, sources: [{ id: "s1", root_alias: "photos", sub_path: "" }] as Array<{ id: string; root_alias: string; sub_path: string }> }));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  useNavigate: () => s.nav,
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}));
vi.mock("../lib/api", async (orig) => {
  const real = await orig<typeof import("../lib/api")>();
  return { ...real, api: { selectionTrash: s.trash, roots: async () => s.roots, sources: async () => s.sources } };
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

  it("[TRASH-017] 回歸：關掉說明後焦點回到按鈕，Esc 只關說明（驗證第 62 輪：按 × 後燈箱的鍵盤沒反應）", async () => {
    s.roots = [{ alias: "photos", mode: "ro" }, { alias: "sorted", mode: "rw" }];
    s.sources = [{ id: "s1", root_alias: "photos", sub_path: "" }];
    const outer = vi.fn();
    wrap(
      <div onKeyDown={(e) => e.key === "Escape" && outer()}>
        <TrashButton select={vi.fn()} />
      </div>,
    );
    const b = await screen.findByRole("button", { name: "移到垃圾桶" });
    await waitFor(() => expect(b.getAttribute("aria-disabled")).toBe("true")); // the library's folders: all read-only
    fireEvent.click(b);
    fireEvent.click(await screen.findByRole("button", { name: "關閉" }));
    expect(document.activeElement).toBe(b);
    fireEvent.click(b);
    fireEvent.keyDown(await screen.findByRole("alert"), { key: "Escape" });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(outer).not.toHaveBeenCalled(); // the Lightbox stays open
    expect(document.activeElement).toBe(b);
  });

  it("[TRASH-018] 回歸：說明打開時焦點進到說明裡；在按鈕上按 Esc 也只關說明，不清掉選取、不關燈箱（驗證第 63 輪）", async () => {
    s.roots = [{ alias: "photos", mode: "ro" }];
    s.sources = [{ id: "s1", root_alias: "photos", sub_path: "" }];
    const outer = vi.fn();
    wrap(
      <div onKeyDown={(e) => e.key === "Escape" && outer()}>
        <TrashButton select={vi.fn()} />
      </div>,
    );
    const b = await screen.findByRole("button", { name: "移到垃圾桶" });
    await waitFor(() => expect(b.getAttribute("aria-disabled")).toBe("true"));
    fireEvent.click(b);
    const card = await screen.findByRole("alert");
    await waitFor(() => expect(card.parentElement?.contains(document.activeElement)).toBe(true)); // focus is in the card
    b.focus();
    fireEvent.keyDown(b, { key: "Escape" }); // Esc with focus still on the button
    expect(screen.queryByRole("alert")).toBeNull();
    expect(outer).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(b);
  });

  it("[TRASH-019] 回歸：唯讀的原因仍寫在按鈕旁，但折到按鈕的寬度，不把選取列撐寬（驗證第 127、128 輪：264 px 的說明使 1024 px 時兩行）", async () => {
    s.roots = [{ alias: "photos", mode: "ro" }];
    s.sources = [{ id: "s1", root_alias: "photos", sub_path: "" }];
    wrap(<TrashButton select={vi.fn()} />);
    const b = await screen.findByRole("button", { name: "移到垃圾桶" });
    await waitFor(() => expect(b.getAttribute("aria-describedby")).toBeTruthy());
    const why = document.getElementById(b.getAttribute("aria-describedby")!)!;
    expect(why.textContent).toContain("唯讀"); // the full sentence, for screen readers (invariant 8)
    // ADR-0055: wrapped under the button it made every bar 34 px taller (round 128); the sentence is not in the flow
    expect(why.className).toContain("sr-only");
  });

  it("[TRASH-020] 回歸：唯讀寫在按鈕裡（「移到垃圾桶 · 唯讀」），按鈕下方沒有東西，完整原因在提示與說明卡（驗證第 128 輪：選取列高 34 px、按鈕高出 21 px）", async () => {
    s.roots = [{ alias: "photos", mode: "ro" }];
    s.sources = [{ id: "s1", root_alias: "photos", sub_path: "" }];
    wrap(<TrashButton select={vi.fn()} />);
    const b = await screen.findByRole("button", { name: "移到垃圾桶" });
    await waitFor(() => expect(b.textContent).toContain("唯讀")); // said where it is seen, on one line
    expect(b.getAttribute("title")).toContain("唯讀的");
    const wrapper = b.parentElement!;
    expect(wrapper.className).not.toContain("flex-col");
    for (const el of [...wrapper.children].filter((x) => x !== b)) expect(el.className).toMatch(/sr-only|trash-popover/);
    fireEvent.click(b);
    expect((await screen.findByRole("alert")).textContent).toContain("唯讀"); // and the card with what to do
  });
});

