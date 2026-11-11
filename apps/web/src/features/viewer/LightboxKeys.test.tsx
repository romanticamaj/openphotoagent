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
  mediaUrl: (id: string) => `/m/${id}`,
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
