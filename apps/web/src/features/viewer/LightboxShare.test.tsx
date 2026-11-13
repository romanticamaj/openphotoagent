import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ favs: new Set<string>(), setFavorite: vi.fn(), setRotation: vi.fn(), select: vi.fn(), trash: vi.fn() }));
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
    setRotation: async (id: string, deg: number) => (s.setRotation(id, deg), { asset_id: id, rotation: deg }),
    roots: async () => [{ alias: "photos", mode: "rw" }],
    createSelection: async (b: { asset_ids: string[] }) => (s.select(b), { id: "sel1", count: 1 }),
    selectionTrash: async (id: string) => (s.trash(id), { plan_id: "p1", job_id: "j1", trash: 1 }),
    assetDuplicates: async (id: string) =>
      id === "p0"
        ? { photos: [{ asset_id: "p0", ch: null }, { asset_id: "d1", ch: null }, { asset_id: "d2", ch: null }] }
        : { photos: [{ asset_id: id, ch: null }] },
  },
}));
const sh = vi.hoisted(() => ({ share: vi.fn(), copy: vi.fn() }));
vi.mock("../../lib/sharePhoto", async (orig) => ({
  ...(await orig<typeof import("../../lib/sharePhoto")>()),
  sharePhoto: sh.share,
  copyPhoto: sh.copy,
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => vi.fn(), // the Lightbox's 移到垃圾桶 opens a plan page (TRASH-013)
}));

import type { GridApi } from "./PhotoGrid";
import { Lightbox } from "./Lightbox";

const grid: GridApi = {
  total: 3,
  ordinal: (p) => p.index,
  at: async (i) => ({ asset: { id: `p${i}`, ch: null, w: 4, h: 3, thumbhash: null, taken_at: null, flags: [] }, pos: { bucket: 0, index: i } }),
};

describe("sharing from the Lightbox (audit round 109: no share or export)", () => {
  const open = () =>
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );

  it("[SHARE-006] 「分享」分享目前這張（含旋轉）；改存成檔案時說存了哪個檔；失敗時說出來", async () => {
    sh.share.mockResolvedValueOnce("saved");
    open();
    (await screen.findByRole("button", { name: "分享" })).click();
    await waitFor(() => expect(sh.share).toHaveBeenCalledWith({ id: "p0", rot: 0, takenAt: null }));
    expect(await screen.findByText("已存成 photo-p0.jpg")).toBeTruthy();
    sh.share.mockRejectedValueOnce(new Error("THUMB_MISSING"));
    screen.getByRole("button", { name: "分享" }).click();
    expect(await screen.findByText("沒能分享這張照片")).toBeTruthy();
  });

  it("[SHARE-007] 「複製圖片」放到剪貼簿並說可以貼上；取消分享不顯示任何訊息", async () => {
    sh.share.mockClear();
    sh.copy.mockClear();
    sh.copy.mockResolvedValueOnce("copied");
    open();
    (await screen.findByRole("button", { name: "複製圖片" })).click();
    await waitFor(() => expect(sh.copy).toHaveBeenCalledWith({ id: "p0", rot: 0, takenAt: null }));
    expect(await screen.findByText("已複製圖片，可以貼到聊天或文件")).toBeTruthy();
    sh.share.mockResolvedValueOnce("cancelled");
    screen.getByRole("button", { name: "分享" }).click();
    await waitFor(() => expect(sh.share).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/已存成|沒能分享/)).toBeNull();
  });
});
