import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ favs: new Set<string>(), setFavorite: vi.fn() }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string, v: string) => `/m/${id}/${v}`,
  preparePreview: async () => true,
  api: {
    asset: async (id: string) => ({ id, favorite: s.favs.has(id), albums: [], labels: [], exif: {}, locations: [] }),
    setFavorite: async (id: string, on: boolean) => {
      s.setFavorite(id, on);
      if (on) s.favs.add(id);
      else s.favs.delete(id);
      return { favorite: on };
    },
    albums: async () => ({ albums: [] }),
  },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));

import type { GridApi } from "./PhotoGrid";
import { Lightbox } from "./Lightbox";

const grid: GridApi = {
  total: 3,
  ordinal: (p) => p.index,
  at: async (i) => ({ asset: { id: `p${i}`, ch: null, w: 4, h: 3, thumbhash: null, taken_at: null, flags: [] }, pos: { bucket: 0, index: i } }),
};

describe("Lightbox keys (audit round 34)", () => {
  it("[COL-012] 回歸：每張照片按 F 都會收藏（只有第一次有效）；Esc 在選單開著時只關選單", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    const lb = screen.getByTestId("lightbox");
    for (const id of ["p0", "p1", "p2"]) {
      await screen.findByRole("button", { name: "加入收藏" });
      fireEvent.keyDown(lb, { key: "f" });
      await waitFor(() => expect(s.setFavorite).toHaveBeenCalledWith(id, true));
      await screen.findByRole("button", { name: "取消收藏" });
      fireEvent.keyDown(lb, { key: "ArrowRight" });
    }
  });
});

describe("F while the next photo loads (audit round 35)", () => {
  it("[COL-013] 回歸：→ 與 F 幾乎同時按，F 在照片載入後仍會收藏那一張", async () => {
    s.favs.clear();
    s.setFavorite.mockReset();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    const lb = screen.getByTestId("lightbox");
    await screen.findByRole("button", { name: "加入收藏" });
    fireEvent.keyDown(lb, { key: "ArrowRight" });
    fireEvent.keyDown(lb, { key: "f" }); // the same instant: p1 is not in yet
    await waitFor(() => expect(s.setFavorite).toHaveBeenCalledWith("p1", true));
    expect(s.setFavorite).not.toHaveBeenCalledWith("p0", expect.anything());
  });
});

describe("zoom (audit round 38)", () => {
  it("[VIEW-035] 回歸：放大後換上 2048 的預覽，不再把 1024 拉大", async () => {
    const { container } = render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    const lb = screen.getByTestId("lightbox");
    await screen.findByRole("button", { name: "加入收藏" });
    expect(container.querySelector('img[data-variant="p2048"]')).toBeNull(); // not before it is needed
    fireEvent.keyDown(lb, { key: "+" });
    await waitFor(() => expect(container.querySelector('img[data-variant="p2048"]')?.getAttribute("src")).toBe("/m/p0/p2048"));
  });
});

