import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ setEdit: vi.fn(), revertEdit: vi.fn(), setRotation: vi.fn(), edit: null as unknown }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  mediaUrl: (id: string, v: string) => `/m/${id}/${v}`,
  preparePreview: async () => true,
  api: {
    asset: async (id: string) => ({ id, favorite: false, albums: [], labels: [], exif: {}, locations: [], edit: s.edit, width: 4000, height: 3000, rotation: 0 }),
    setRotation: async (id: string, deg: number) => (s.setRotation(id, deg), { rotation: deg }),
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

  it("[EDIT-018] 回歸：從搜尋開啟（沒有長寬）也能裁切，用照片本身的長寬（驗證第 145 輪：從搜尋裁切沒有作用）", async () => {
    s.setEdit.mockReset();
    s.edit = null;
    const fromSearch: GridApi = { total: 1, ordinal: (p) => p.index, at: async () => ({ asset: { id: "q0", ch: null, w: null, h: null, thumbhash: null, taken_at: null, flags: [] }, pos: { bucket: 0, index: 0 } }) };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={fromSearch} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "編輯" }));
    await screen.findByRole("slider", { name: "曝光" });
    await waitFor(() => fireEvent.click(screen.getByRole("radio", { name: "1:1" })));
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    await waitFor(() => expect(s.setEdit).toHaveBeenCalledWith("q0", { v: 1, crop: { x: 0.125, y: 0, w: 0.75, h: 1 } }));
  });

  it("[EDIT-021] 回歸：編輯中 Esc 只取消編輯，← → 不換照片（驗證第 145 輪：Esc 關掉整個燈箱，→ 帶著沒存的編輯到下一張）", async () => {
    s.edit = null;
    const close = vi.fn();
    const two: GridApi = { ...grid, total: 2 };
    const { container } = render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={two} onClose={close} />
      </QueryClientProvider>,
    );
    const lb = screen.getByTestId("lightbox");
    fireEvent.click(await screen.findByRole("button", { name: "編輯" }));
    await screen.findByRole("slider", { name: "曝光" });
    fireEvent.keyDown(lb, { key: "ArrowRight" });
    expect((container.querySelector('img[data-variant="t512"]') as HTMLImageElement).src).toContain("/m/p0/");
    fireEvent.keyDown(lb, { key: "Escape" });
    expect(close).not.toHaveBeenCalled();
    expect(screen.queryByRole("slider", { name: "曝光" })).toBeNull();
  });

  it("[EDIT-019] 回歸：已裁切的照片按 R，裁切跟著轉（驗證第 145 輪）", async () => {
    s.setEdit.mockReset();
    s.edit = { v: 1, crop: { x: 0, y: 0, w: 0.5, h: 1 } };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    await screen.findByRole("button", { name: "編輯" });
    await new Promise((r) => setTimeout(r, 50)); // its saved edit has come
    fireEvent.keyDown(screen.getByTestId("lightbox"), { key: "r" });
    await waitFor(() => expect(s.setEdit).toHaveBeenCalledWith("p0", { v: 1, crop: { x: 0, y: 0, w: 1, h: 0.5 } }));
    s.edit = null;
  });

  it("[EDIT-023] 回歸：「完成」說已儲存並可復原（驗證第 145 輪：沒有任何訊息也不能復原）", async () => {
    s.setEdit.mockReset();
    s.revertEdit.mockReset();
    s.edit = null;
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "編輯" }));
    fireEvent.change(await screen.findByRole("slider", { name: "對比" }), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    expect(await screen.findByText("已儲存編輯")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "復原" }));
    await waitFor(() => expect(s.revertEdit).toHaveBeenCalledWith("p0")); // back to as shot, as it was
  });

  it("[EDIT-024] 回歸：編輯提示按「復原」或 × 之後焦點回到燈箱，按鍵照常（驗證第 146 輪：焦點掉到頁面、E 和 Esc 都沒反應）", async () => {
    s.edit = null;
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "編輯" }));
    fireEvent.change(await screen.findByRole("slider", { name: "對比" }), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    const undo = await screen.findByRole("button", { name: "復原" });
    await new Promise((r) => setTimeout(r, 50)); // 完成's own focus move has run, as it has before a person presses 復原
    undo.focus(); // pressed from the keyboard: the button had focus, and then it was gone
    fireEvent.click(undo);
    const lb = screen.getByTestId("lightbox");
    await waitFor(() => expect(lb.contains(document.activeElement)).toBe(true));
    fireEvent.keyDown(document.activeElement!, { key: "e" });
    expect(await screen.findByRole("slider", { name: "曝光" })).toBeTruthy(); // the keys work again
  });
});
