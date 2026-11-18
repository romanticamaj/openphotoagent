import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ setEdit: vi.fn(), revertEdit: vi.fn() }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string, v: string) => `/m/${id}/${v}`,
  preparePreview: async () => true,
  api: {
    asset: async (id: string) => ({ id, favorite: false, albums: [], labels: [], exif: {}, locations: [], edit: null, width: 4000, height: 3000, rotation: 0 }),
    setEdit: async (id: string, e: unknown) => (s.setEdit(id, e), { asset_id: id, edit: e }),
    revertEdit: async (id: string) => (s.revertEdit(id), { asset_id: id, edit: null }),
    albums: async () => ({ albums: [] }),
    roots: async () => [{ alias: "photos", mode: "rw" }],
    assetDuplicates: async (id: string) => ({ photos: [{ asset_id: id, ch: null }] }),
  },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => vi.fn(),
}));

import type { GridApi } from "./PhotoGrid";
import { Lightbox } from "./Lightbox";

const grid: GridApi = {
  total: 1,
  ordinal: (p) => p.index,
  at: async (i) => ({ asset: { id: `p${i}`, ch: null, w: 4000, h: 3000, thumbhash: null, taken_at: null, flags: [] }, pos: { bucket: 0, index: i } }),
};

describe("editing in the Lightbox (ADR-0057)", () => {
  it("[EDIT-012] E 或「編輯」打開編輯，改動立刻畫在照片上，「完成」存起來；Esc 只取消編輯、不關照片", async () => {
    const close = vi.fn();
    const { container } = render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={close} />
      </QueryClientProvider>,
    );
    const lb = screen.getByTestId("lightbox");
    await screen.findByRole("button", { name: "編輯" });
    fireEvent.keyDown(lb, { key: "e" });
    const exp = await screen.findByRole("slider", { name: "曝光" });
    fireEvent.change(exp, { target: { value: "0.5" } });
    const img = () => container.querySelector('img[data-variant="t512"]') as HTMLImageElement;
    await waitFor(() => expect(img().style.filter).toMatch(/#opa-e-/));
    fireEvent.keyDown(exp, { key: "Escape" });
    expect(close).not.toHaveBeenCalled();
    expect(screen.queryByRole("slider", { name: "曝光" })).toBeNull();
    await waitFor(() => expect(img().style.filter).toBe("")); // cancelled: as before
    fireEvent.click(screen.getByRole("button", { name: "編輯" }));
    fireEvent.change(await screen.findByRole("slider", { name: "對比" }), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    await waitFor(() => expect(s.setEdit).toHaveBeenCalledWith("p0", { v: 1, contrast: 30 }));
    expect(screen.queryByRole("slider", { name: "對比" })).toBeNull();
    expect(img().style.filter).toMatch(/#opa-e-/); // kept
  });
});
