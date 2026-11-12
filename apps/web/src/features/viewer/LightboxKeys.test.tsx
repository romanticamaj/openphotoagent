import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  },
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

describe("rotate (audit round 26 gap)", () => {
  it("[ROT-003] R 或「旋轉」把照片轉 90 度：立刻顯示、存在圖庫、檔案不動", async () => {
    s.setRotation.mockReset();
    const { container } = render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    const lb = screen.getByTestId("lightbox");
    await screen.findByRole("button", { name: "加入收藏" });
    fireEvent.keyDown(lb, { key: "r" });
    await waitFor(() => expect(s.setRotation).toHaveBeenCalledWith("p0", 90));
    const img = container.querySelector('img[data-variant="t512"]') as HTMLImageElement;
    expect(img.style.transform).toContain("rotate(90deg)");
    fireEvent.click(screen.getByRole("button", { name: "旋轉" }));
    await waitFor(() => expect(s.setRotation).toHaveBeenCalledWith("p0", 180));
  });
});


describe("a photo already turned (audit round 47)", () => {
  it("[ROT-005] 回歸：從搜尋開啟已轉 90 度的照片，Lightbox 依這個角度顯示，再按 R 存 180 度", async () => {
    s.setRotation.mockReset();
    const turned: GridApi = {
      total: 1,
      ordinal: (p) => p.index,
      at: async () => ({ asset: { id: "t0", ch: null, w: 3, h: 4, thumbhash: null, taken_at: null, flags: [], rot: 90 }, pos: { bucket: 0, index: 0 } }),
    };
    const { container } = render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={turned} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    await screen.findByRole("button", { name: "加入收藏" });
    expect((container.querySelector('img[data-variant="t512"]') as HTMLImageElement).style.transform).toContain("rotate(90deg)");
    fireEvent.keyDown(screen.getByTestId("lightbox"), { key: "r" });
    await waitFor(() => expect(s.setRotation).toHaveBeenCalledWith("t0", 180));
  });
});

describe("a slideshow and the shortcut list (audit round 50)", () => {
  it("[VIEW-039] 空白鍵或「幻燈片」每 4 秒換下一張，到最後一張停；按 ← → 或空白鍵就停", async () => {
    vi.useFakeTimers();
    // React commits on its own scheduler, not on fake timers: each step lets React finish inside act(), or the next
    // 4 s timer started late under load and the step was missed
    const tick = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
    try {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <Lightbox start={0} grid={grid} onClose={vi.fn()} />
        </QueryClientProvider>,
      );
      const lb = screen.getByTestId("lightbox");
      const pos = () => screen.getByTestId("lightbox-position").textContent;
      fireEvent.click(screen.getByRole("button", { name: "幻燈片" }));
      expect(screen.getByRole("button", { name: "停止幻燈片" }).getAttribute("aria-pressed")).toBe("true");
      await tick(4000);
      expect(pos()).toBe("第 2 張，共 3 張");
      await tick(4000);
      expect(pos()).toBe("第 3 張，共 3 張");
      await tick(4000); // the last one: it stops there
      expect(pos()).toBe("第 3 張，共 3 張");
      expect(screen.getByRole("button", { name: "幻燈片" })).toBeTruthy();
      fireEvent.keyDown(lb, { key: "ArrowLeft" });
      fireEvent.keyDown(lb, { key: "ArrowLeft" });
      fireEvent.keyDown(lb, { key: " " }); // Space plays
      await tick(4000);
      expect(pos()).toBe("第 2 張，共 3 張");
      fireEvent.keyDown(lb, { key: "ArrowLeft" }); // moving by hand stops it
      await tick(8000);
      expect(pos()).toBe("第 1 張，共 3 張");
    } finally {
      vi.useRealTimers();
    }
  });

  it("[VIEW-040] 「?」列出快捷鍵；Esc 只關掉清單", async () => {
    const onClose = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={onClose} />
      </QueryClientProvider>,
    );
    const lb = screen.getByTestId("lightbox");
    fireEvent.keyDown(lb, { key: "?" });
    const list = screen.getByRole("dialog", { name: "快捷鍵" });
    for (const k of ["←", "→", "空白鍵", "F", "R", "I", "Esc"]) expect(list.textContent).toContain(k);
    fireEvent.keyDown(list, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "快捷鍵" })).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "快捷鍵" }));
    expect(screen.getByRole("dialog", { name: "快捷鍵" })).toBeTruthy();
  });
});


describe("Tab stays in the Lightbox (audit round 51)", () => {
  it("[A11Y-012] 回歸：剛開啟就按 Shift+Tab 或 Tab，焦點仍在燈箱裡（驗證第 51 輪：跑到後面的頁面，Esc 與方向鍵都失效）", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    const lb = screen.getByTestId("lightbox");
    await screen.findByRole("button", { name: "加入收藏" });
    lb.focus();
    fireEvent.keyDown(lb, { key: "Tab", shiftKey: true });
    expect(lb.contains(document.activeElement) && document.activeElement !== lb).toBe(true);
    lb.focus();
    fireEvent.keyDown(lb, { key: "Tab" });
    expect(lb.contains(document.activeElement) && document.activeElement !== lb).toBe(true);
  });
});

describe("Delete in the Lightbox (audit round 61)", () => {
  it("[VIEW-047] Delete 鍵就是「移到垃圾桶」：為這張照片建立計畫（不會直接移動）；快捷鍵清單列出它", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={1} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    const lb = screen.getByTestId("lightbox");
    await screen.findByRole("button", { name: "移到垃圾桶" });
    fireEvent.keyDown(lb, { key: "Delete" });
    await waitFor(() => expect(s.trash).toHaveBeenCalledWith("sel1"));
    expect(s.select).toHaveBeenCalledWith({ asset_ids: ["p1"] });
    fireEvent.keyDown(lb, { key: "?" });
    expect(screen.getByRole("dialog", { name: "快捷鍵" }).textContent).toContain("移到垃圾桶");
  });
});

