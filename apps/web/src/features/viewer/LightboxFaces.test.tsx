import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ faces: ["p0"] as string[] }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string, v: string) => `/m/${id}/${v}`,
  preparePreview: async () => true,
  api: {
    asset: async (id: string) => ({ id, favorite: false, albums: [], labels: [], exif: {}, locations: [], edit: null }),
    albums: async () => ({ albums: [] }),
    roots: async () => [{ alias: "photos", mode: "rw" }],
    assetDuplicates: async (id: string) => ({ photos: [{ asset_id: id, ch: null }] }),
    facesPresent: async (ids: string[]) => ({ enabled: true, with_faces: ids.filter((i) => s.faces.includes(i)) }),
  },
}));
const sh = vi.hoisted(() => ({ share: vi.fn(async () => "shared"), copy: vi.fn(async () => "copied") }));
vi.mock("../../lib/sharePhoto", async (orig) => ({
  ...(await orig<typeof import("../../lib/sharePhoto")>()),
  sharePhoto: sh.share,
  copyPhoto: sh.copy,
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => vi.fn(),
}));

import type { GridApi } from "./PhotoGrid";
import { Lightbox } from "./Lightbox";

const grid: GridApi = {
  total: 2,
  ordinal: (p) => p.index,
  at: async (i) => ({ asset: { id: `p${i}`, ch: null, w: 4, h: 3, thumbhash: null, taken_at: null, flags: [] }, pos: { bucket: 0, index: i } }),
};

describe("one photo with faces (ADR-0049 addendum 8)", () => {
  it("[SHARE-044] 回歸：燈箱裡單張的分享與複製也先查人臉，有臉時先問，取消就不分享（驗證第 146 輪：只有多張分享會查）", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "分享" }));
    const ask = await screen.findByRole("alertdialog");
    expect(ask.textContent).toContain("這張照片有人臉");
    expect(sh.share).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "複製圖片" }));
    await screen.findByRole("alertdialog");
    expect(sh.copy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    s.faces = []; // a photo with none: shared at once
    fireEvent.click(screen.getByRole("button", { name: "分享" }));
    await waitFor(() => expect(sh.share).toHaveBeenCalled());
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});
