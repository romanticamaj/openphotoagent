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

import { OnThisDay } from "./OnThisDay";

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
    expect(open).toHaveBeenCalledWith(["a", "b"], 1);
    fireEvent.click(screen.getByRole("button", { name: "今天先不看" }));
    expect(screen.queryByRole("heading", { name: "那年今日" })).toBeNull();
  });

  it("[MEM-003] 沒有往年的照片時不佔位置", async () => {
    s.groups = [];
    const { container } = wrap();
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
  });
});
