import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ groups: [] as unknown[], scope: undefined as string | undefined, asked: 0 }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: { onThisDay: async () => (s.asked++, { today: "2026-11-12", groups: s.groups, scope: s.scope }) },
}));

import { memoryGrid, OnThisDay, tilesFor } from "./OnThisDay";

const wrap = (onOpen = vi.fn()) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <OnThisDay onOpen={onOpen} />
    </QueryClientProvider>,
  );

describe("那年今日 (10 §2.8)", () => {
  it("[MEM-003] 每一年一列「N 年前的今天」，點照片從那一年開始看", async () => {
    s.groups = [
      { year: 2025, years_ago: 1, photos: [{ asset_id: "a", ch: null, taken_at: "2025-11-10T09:00:00" }, { asset_id: "b", ch: null, taken_at: null }] },
      { year: 2023, years_ago: 3, photos: [{ asset_id: "c", ch: null, taken_at: null }] },
    ];
    const open = vi.fn();
    wrap(open);
    expect(await screen.findByRole("heading", { name: "那年今日" })).toBeTruthy();
    expect(screen.getByText("1 年前的今天")).toBeTruthy();
    expect(screen.getByText("3 年前的今天")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: /2025/ })[1]);
    expect(open).toHaveBeenCalledWith([{ id: "a", rot: undefined }, { id: "b", rot: undefined }], 1);
    fireEvent.click(screen.getByRole("button", { name: "今天先不看" }));
    expect(screen.queryByRole("heading", { name: "那年今日" })).toBeNull();
  });

  it("[MEM-003] 沒有往年的照片時不佔位置", async () => {
    s.groups = [];
    const { container } = wrap();
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
  });

  it("[MEM-007] 回歸：「+N」可以點，從第 9 張開始看（驗證第 49 輪：只是文字）", async () => {
    window.localStorage.clear(); // 「今天先不看」 from the test before
    s.groups = [{ year: 2024, years_ago: 2, photos: Array.from({ length: 11 }, (_, i) => ({ asset_id: `p${i}`, ch: null, taken_at: null })) }];
    const open = vi.fn();
    wrap(open);
    fireEvent.click(await screen.findByRole("button", { name: "看 2024 年的其他 3 張" }));
    expect(open).toHaveBeenCalledWith(expect.arrayContaining([{ id: "p8", rot: undefined }]), 8);
  });

  it("[MEM-008] 回歸：列外的照片在燈箱裡也看得到（驗證第 50 輪：第 9～24 張是破圖）", async () => {
    window.localStorage.clear();
    s.groups = [{ year: 2024, years_ago: 2, photos: Array.from({ length: 10 }, (_, i) => ({ asset_id: `p${i}`, ch: `h${i}`, taken_at: null, rot: 90 })) }];
    const open = vi.fn();
    wrap(open);
    fireEvent.click(await screen.findByRole("button", { name: "看 2024 年的其他 2 張" }));
    const [photos, start] = open.mock.calls[0] as [Array<{ id: string; ch?: string; rot?: number }>, number];
    // the hash goes with each photo: without it the desktop app had no thumbnail path for photos the row never drew
    const at = await memoryGrid(photos).at(start);
    expect(at?.asset).toMatchObject({ id: "p8", ch: "h8", rot: 90 });
  });

  it("[MEM-009] 回歸：往下捲動時讓出位置，回到頂端再出現（驗證第 51～53 輪：一直釘在時間軸上方）", async () => {
    window.localStorage.clear();
    s.groups = [{ year: 2025, years_ago: 1, photos: [{ asset_id: "a", ch: null, taken_at: null }] }];
    const qc = new QueryClient();
    const { rerender } = render(
      <QueryClientProvider client={qc}>
        <OnThisDay onOpen={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "那年今日" })).toBeTruthy();
    rerender(
      <QueryClientProvider client={qc}>
        <OnThisDay onOpen={vi.fn()} away />
      </QueryClientProvider>,
    );
    expect(screen.queryByRole("heading", { name: "那年今日" })).toBeNull();
    rerender(
      <QueryClientProvider client={qc}>
        <OnThisDay onOpen={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("heading", { name: "那年今日" })).toBeTruthy();
  });

  it("[MEM-027] 回歸：前後三天都沒有舊照片時，卡片寫「那年這個月」與「N 年前的這個月」（驗證第 135 輪：11 月 18 日什麼都沒出現）", async () => {
    s.scope = "month";
    s.groups = [{ year: 2025, years_ago: 1, total: 12, photos: [{ asset_id: "a", ch: null, taken_at: null }] }];
    wrap();
    expect(await screen.findByRole("heading", { name: "那年這個月" })).toBeTruthy();
    expect(screen.getByText("1 年前的這個月")).toBeTruthy();
    s.scope = undefined;
  });

  it("[MEM-031] 回歸：三年時每一年少放幾張，三年都看得到；其餘從「+N」開（驗證第 136 輪：1280 px 第三年被切掉、沒有捲動提示）", async () => {
    const year = (y: number, n: number) => ({ year: y, years_ago: 2026 - y, photos: Array.from({ length: n }, (_, i) => ({ asset_id: `${y}-${i}`, ch: null, taken_at: null })) });
    s.groups = [year(2025, 24), year(2024, 10), year(2023, 10)];
    const open = vi.fn();
    wrap(open);
    await screen.findByRole("heading", { name: "那年今日" });
    for (const y of [2025, 2024, 2023]) expect(screen.getAllByRole("button", { name: new RegExp(`^${y}`) })).toHaveLength(4);
    fireEvent.click(screen.getByRole("button", { name: /2025.*20/ })); // 「+20」 opens where the row stops
    expect(open.mock.calls[0]![1]).toBe(4);
    s.groups = [year(2025, 24)];
  });

  it("[MEM-032] 回歸：卡片等時間軸的第一批照片之後才去拿（驗證第 136 輪：有卡片時第一張照片 266 ms、沒有時 196 ms）", async () => {
    s.groups = [{ year: 2025, years_ago: 1, photos: [{ asset_id: "a", ch: null, taken_at: null }] }];
    const before = s.asked;
    wrap();
    expect(s.asked).toBe(before); // not asked while the timeline's first photos load
    expect(await screen.findByRole("heading", { name: "那年今日" })).toBeTruthy();
    expect(s.asked).toBe(before + 1);
  });

  it("[MEM-034] 回歸：放不下時年份換行，不藏在捲軸後面（驗證第 137 輪：1024 px 第三年被切掉 135 px）", async () => {
    const { readFileSync } = await import("node:fs");
    const css = readFileSync(`${process.cwd()}/src/styles.css`, "utf-8").replace(/\s+/g, " ");
    const rule = css.match(/\.on-this-day-years \{([^}]*)\}/)?.[1] ?? "";
    expect(rule).toContain("flex-wrap: wrap");
    expect(rule).not.toContain("overflow-x: auto");
  });
  it("[MEM-035] 回歸：「今天先不看」之後焦點到時間軸，不落到頁面（驗證第 137 輪）", async () => {
    s.groups = [{ year: 2025, years_ago: 1, photos: [{ asset_id: "a", ch: null, taken_at: null }] }];
    const grid = document.createElement("div");
    grid.setAttribute("role", "grid");
    grid.tabIndex = -1;
    const tile = document.createElement("div");
    tile.setAttribute("role", "gridcell");
    tile.tabIndex = 0; // the grid's current photo (roving tabindex)
    grid.appendChild(tile);
    document.body.appendChild(grid);
    wrap();
    fireEvent.click(await screen.findByRole("button", { name: "今天先不看" }));
    // its current photo, not the grid: → then went to photo 2 and no ring showed (round 138, MEM-035 revised)
    await waitFor(() => expect(document.activeElement).toBe(tile));
    grid.remove();
  });

  it("[MEM-036] 回歸：每年幾張由卡片的寬度決定，三年放在同一列（驗證第 138 輪：1024 px 換成兩列，卡片高 252 px）", () => {
    expect(tilesFor(1248, 3)).toBe(4); // 1280 px: as before
    expect(tilesFor(780, 3)).toBe(2); // 1024 px beside the sidebar: 2 a year, one row
    expect(tilesFor(400, 3)).toBe(2); // never fewer than 2: then the years wrap (MEM-034)
    expect(tilesFor(1248, 1)).toBe(8);
    expect(tilesFor(undefined, 2)).toBe(6); // not measured yet: by the number of years (MEM-031)
  });

  it("[MEM-043] 回歸：每一年的標題分兩行（「N 年前…」一行、年份與張數一行），三年在 758 px 放得下（驗證第 139 輪：標題一行 247 px，三年要 801 px）", async () => {
    window.localStorage.clear(); // MEM-035 hid it for today
    s.scope = "month";
    s.groups = [{ year: 2025, years_ago: 1, total: 424, photos: Array.from({ length: 24 }, (_, i) => ({ asset_id: `x${i}`, ch: null, taken_at: null })) }];
    wrap();
    const first = await screen.findByText("1 年前的這個月");
    const head = first.closest(".on-this-day-year-head");
    expect(head?.children).toHaveLength(2);
    expect(head?.children[1]?.textContent).toContain("2025");
    expect(head?.children[1]?.textContent).toContain("424");
    const { readFileSync } = await import("node:fs");
    const css = readFileSync(`${process.cwd()}/src/styles.css`, "utf-8").replace(/\s+/g, " ");
    expect(css).toMatch(/\.on-this-day-year-head \{[^}]*flex-direction: column/);
    expect(3 * Math.max(150, tilesFor(758, 3) * 76 + 52) + 2 * 24).toBeLessThanOrEqual(758);
    s.scope = undefined;
  });
});
