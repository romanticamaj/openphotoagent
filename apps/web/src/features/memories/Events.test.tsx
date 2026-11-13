import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ events: [] as unknown[] }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string) => `/m/${id}`,
  api: { memoryEvents: async () => ({ events: s.events }) },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ children, search }: { children: React.ReactNode; search?: Record<string, string> }) => <a data-search={JSON.stringify(search)}>{children}</a>,
}));

import { Events } from "./Events";

describe("活動 (ADR-0049 addendum 2)", () => {
  it("[MEM-017] 每個拍了很多照片的日子一張卡：日期與主題、張數，點封面看那天的照片，「看全部」搜那一天", async () => {
    s.events = [
      { day: "2025-11-13", key: "activity.soccer", name: "足球", count: 16, photos: [{ asset_id: "a", ch: null }, { asset_id: "b", ch: null }] },
      { day: "2026-03-02", key: null, name: null, count: 20, photos: [{ asset_id: "c", ch: null }] },
    ];
    const open = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Events onOpen={open} />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "最近的活動" })).toBeTruthy();
    expect(screen.getByText("2025/11/13 · 足球")).toBeTruthy();
    expect(screen.getByText("2026/3/2")).toBeTruthy(); // no subject: the date alone
    fireEvent.click(screen.getByRole("button", { name: "2025/11/13 · 足球，16 張" }));
    expect(open).toHaveBeenCalledWith([{ id: "a", rot: undefined }, { id: "b", rot: undefined }], 0);
    expect(JSON.parse(screen.getAllByText("看全部")[0].closest("a")?.getAttribute("data-search") ?? "{}")).toEqual({ q: "2025年11月13日" });
  });
});
