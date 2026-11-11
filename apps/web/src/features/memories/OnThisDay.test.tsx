import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ groups: [] as unknown[] }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: { onThisDay: async () => ({ today: "2026-11-12", groups: s.groups }) },
}));

import { memoryGrid, OnThisDay } from "./OnThisDay";

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
});

