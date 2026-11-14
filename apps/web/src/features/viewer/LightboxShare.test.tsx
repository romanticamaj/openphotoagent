import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({ createAlbum: vi.fn(), addToAlbum: vi.fn(), favs: new Set<string>(), setFavorite: vi.fn(), setRotation: vi.fn(), select: vi.fn(), trash: vi.fn() }));
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
    createAlbum: async (name: string) => (s.createAlbum(name), { id: "al1", name: name.replaceAll("/", "_") }), // as the server names it (INJ-017)
    addToAlbum: async (id: string, ids: string[]) => (s.addToAlbum(id, ids), { count: ids.length }),
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
    expect(await screen.findByText("已存到「下載」資料夾：photo-p0.jpg")).toBeTruthy(); // which folder too (audit round 110)
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

describe("the Lightbox's toast (audit round 111)", () => {
  it("[SHARE-010] 回歸：Lightbox 裡的提示在底片列上方，不蓋住底片列（驗證第 111 輪）", async () => {
    const { readFileSync } = await import("node:fs");
    const css = readFileSync(`${process.cwd()}/src/styles.css`, "utf-8");
    const strip = Number(/\.filmstrip button\s*\{[^}]*height:\s*(\d+)px/.exec(css)![1]) + 16; // tile + padding
    const m = /\.lightbox \.toast\s*\{[^}]*bottom:\s*(\d+)px/.exec(css);
    expect(m).not.toBeNull();
    expect(Number(m![1])).toBeGreaterThan(strip);
  });
});

describe("a Lightbox opened to play (audit round 109)", () => {
  it("[VIEW-061] 以「播放」打開時幻燈片已在播放；一般打開時沒有", async () => {
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay />
      </QueryClientProvider>,
    );
    expect((await screen.findByRole("button", { name: "停止幻燈片" })).getAttribute("aria-pressed")).toBe("true");
    unmount();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    expect((await screen.findByRole("button", { name: "播放幻燈片" })).getAttribute("aria-pressed")).toBe("false");
  });
});

describe("a slideshow that plays as a story (audit round 112)", () => {
  it("[VIEW-062] 回歸：播放中不逐張唸出位置；停止時按鈕叫「播放幻燈片」（驗證第 112 輪：每一步都唸、按鈕只叫「幻燈片」）", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay />
      </QueryClientProvider>,
    );
    await screen.findByRole("button", { name: "停止幻燈片" });
    expect(screen.getByTestId("lightbox-position").getAttribute("aria-live")).toBe("off");
    screen.getByRole("button", { name: "停止幻燈片" }).click();
    await screen.findByRole("button", { name: "播放幻燈片" });
    expect(screen.getByTestId("lightbox-position").getAttribute("aria-live")).toBe("polite");
  });

  it("[VIEW-063] 回歸：以「播放」打開時先顯示標題卡，播放中是故事模式（控制列淡出、照片淡入）（驗證第 112 輪：沒有標題、硬切）", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay title="2025/11/11–11/12 · 足球" />
      </QueryClientProvider>,
    );
    const card = await screen.findByTestId("story-title");
    expect(card.textContent).toContain("2025/11/11–11/12 · 足球");
    expect(card.getAttribute("aria-hidden")).toBe("true"); // the dialog's name says it; not read twice
    expect(screen.getByTestId("lightbox").className).toContain("story");
    screen.getByRole("button", { name: "停止幻燈片" }).click();
    await screen.findByRole("button", { name: "播放幻燈片" });
    expect(screen.getByTestId("lightbox").className).not.toContain("story"); // stopped: every control back
    expect(screen.queryByTestId("story-title")).toBeNull();
  });
});

describe("focus on ★ as the photo changes (audit round 113)", () => {
  it("[COL-030] 回歸：焦點在 ★ 時換照片，焦點留在 ★（驗證第 113 輪：掉到頁面，下一個 Tab 跑到背後的「跳到主要內容」）", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    const star = await screen.findByRole("button", { name: "加入收藏" });
    star.focus();
    fireEvent.keyDown(screen.getByTestId("lightbox"), { key: "ArrowRight" });
    await waitFor(() => expect(screen.getByTestId("lightbox-position").textContent).toContain("2"));
    expect(document.activeElement).toBe(star); // the same button, kept while the next photo's state comes
    await waitFor(() => expect(star.getAttribute("aria-busy")).toBeNull());
    expect(document.activeElement).toBe(star);
  });
});

describe("the end of a story, and a pointer that moves (audit round 113)", () => {
  it("[VIEW-064] 回歸：播完最後一張出現結尾卡「再播一次」「關閉」，焦點在「再播一次」；按了從第 1 張重播（驗證第 113 輪：停在 12/12 沒有結尾）", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const tick = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
    try {
      const onClose = vi.fn();
      render(
        <QueryClientProvider client={new QueryClient()}>
          <Lightbox start={0} grid={grid} onClose={onClose} autoplay title="足球" />
        </QueryClientProvider>,
      );
      await screen.findByRole("button", { name: "停止幻燈片" });
      expect(screen.queryByRole("button", { name: "再播一次" })).toBeNull();
      await tick(4000);
      await tick(4000);
      await tick(4000); // past the last photo
      const again = await screen.findByRole("button", { name: "再播一次" });
      expect(screen.getByText("播完了")).toBeTruthy();
      expect(document.activeElement).toBe(again);
      fireEvent.click(again);
      expect(screen.getByTestId("lightbox-position").textContent).toBe("第 1 張，共 3 張");
      expect(screen.getByRole("button", { name: "停止幻燈片" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "再播一次" })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("[VIEW-065] 回歸：播放中移動滑鼠，控制列暫時回來（驗證第 113 輪：只有滑到控制列本身才出現）", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay />
      </QueryClientProvider>,
    );
    await screen.findByRole("button", { name: "停止幻燈片" });
    const lb = screen.getByTestId("lightbox");
    expect(lb.className).toContain("story");
    fireEvent.mouseMove(lb);
    expect(lb.className).not.toContain("story");
    expect(screen.getByRole("button", { name: "停止幻燈片" })).toBeTruthy(); // still playing
  });
});

describe("playing it again (audit round 114)", () => {
  async function toEnd() {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const tick = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay title="足球" />
      </QueryClientProvider>,
    );
    await screen.findByRole("button", { name: "停止幻燈片" });
    for (let i = 0; i < 3; i++) await tick(4000);
    return screen.findByRole("button", { name: "再播一次" });
  }

  it("[VIEW-066] 回歸：按「再播一次」後焦點留在 Lightbox 裡（驗證第 114 輪：焦點掉出去，→、空白鍵、Esc 都沒反應）", async () => {
    try {
      fireEvent.click(await toEnd());
      const lb = screen.getByTestId("lightbox");
      expect(lb.contains(document.activeElement)).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("[VIEW-067] 回歸：結尾卡上按空白鍵等於「再播一次」：從第 1 張重播、結尾卡消失（驗證第 114 輪：只切換播放，結尾卡還在）", async () => {
    try {
      await toEnd();
      fireEvent.keyDown(screen.getByTestId("lightbox"), { key: " " });
      expect(screen.getByTestId("lightbox-position").textContent).toBe("第 1 張，共 3 張");
      expect(screen.getByRole("button", { name: "停止幻燈片" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "再播一次" })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Space on a button presses the button (audit round 115)", () => {
  it("[VIEW-068] 回歸：焦點在按鈕上時空白鍵按的是那個按鈕，不是幻燈片；焦點在 Lightbox 本身時才是幻燈片（驗證第 115 輪：在「關閉」上按空白鍵卻重播）", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    const star = await screen.findByRole("button", { name: "加入收藏" });
    star.focus();
    const space = fireEvent.keyDown(star, { key: " " });
    expect(space).toBe(true); // not taken: the browser presses the button
    expect(screen.getByRole("button", { name: "播放幻燈片" })).toBeTruthy();
    screen.getByTestId("lightbox").focus();
    fireEvent.keyDown(screen.getByTestId("lightbox"), { key: " " });
    expect(screen.getByRole("button", { name: "停止幻燈片" })).toBeTruthy();
  });
});

describe("a story to keep (audit round 115)", () => {
  async function toEnd(seeAll?: { n: number; go: () => void }) {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const tick = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay title="2025 的足球" seeAll={seeAll} />
      </QueryClientProvider>,
    );
    await screen.findByRole("button", { name: "停止幻燈片" });
    for (let i = 0; i < 3; i++) await tick(4000);
    return screen.findByRole("button", { name: "再播一次" });
  }

  it("[VIEW-069] 回歸：結尾卡可「存成相簿」（以標題為名，放入播過的照片）與「看全部 N 張」（驗證第 115 輪：播完什麼都留不下）", async () => {
    const go = vi.fn();
    try {
      await toEnd({ n: 120, go });
      expect(screen.getByText("播放了 3 張，共 120 張")).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "存成相簿（3 張）" }));
      await waitFor(() => expect(s.addToAlbum).toHaveBeenCalledWith("al1", ["p0", "p1", "p2"]));
      expect(s.createAlbum).toHaveBeenCalledWith("2025 的足球");
      expect(await screen.findByText("已存成相簿「2025 的足球」")).toBeTruthy();
      expect(screen.queryByRole("button", { name: /存成相簿/ })).toBeNull(); // kept once, not twice
      fireEvent.click(screen.getByRole("button", { name: "看全部 120 張" }));
      expect(go).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("[VIEW-070] 回歸：播放中頂端有進度段落；結尾卡後方的控制列、底片列與箭頭隱藏（驗證第 115 輪：結尾卡壓在控制列上）", async () => {
    try {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay title="足球" />
        </QueryClientProvider>,
      );
      await screen.findByRole("button", { name: "停止幻燈片" });
      const bar = screen.getByTestId("story-progress");
      expect(bar.children).toHaveLength(3);
      expect(bar.getAttribute("aria-hidden")).toBe("true");
      cleanup();
      await toEnd();
      expect(screen.getByTestId("lightbox").className).toContain("ended");
      expect(screen.queryByRole("button", { name: "加入收藏" })).toBeNull(); // hidden, out of the Tab order too
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("the end card's keyboard and album name (audit round 116)", () => {
  async function toEnd(title: string) {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const tick = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay title={title} seeAll={{ n: 17, go: vi.fn() }} />
      </QueryClientProvider>,
    );
    await screen.findByRole("button", { name: "停止幻燈片" });
    for (let i = 0; i < 3; i++) await tick(4000);
    return screen.findByRole("button", { name: "再播一次" });
  }

  it("[VIEW-071] 回歸：結尾卡上 Tab 在卡片的按鈕間循環，不跑到隱藏的控制列或 Lightbox 外（驗證第 116 輪：從「關閉」Tab 到背後的頁面）", async () => {
    try {
      const again = await toEnd("足球");
      const lb = screen.getByTestId("lightbox");
      const close = screen.getAllByRole("button", { name: "關閉" }).at(-1)!;
      close.focus();
      fireEvent.keyDown(lb, { key: "Tab" });
      expect(document.activeElement).toBe(again); // from the last of the card to its first
      fireEvent.keyDown(lb, { key: "Tab", shiftKey: true });
      expect(document.activeElement).toBe(close); // and back
    } finally {
      vi.useRealTimers();
    }
  });

  it("[VIEW-072] 回歸：存成的相簿名稱與畫面所說的一致：日期的斜線改成連字號，狀態列寫伺服器存下的名稱（驗證第 116 輪：說「2026/1/26」，存成「2026_1_26」）", async () => {
    try {
      await toEnd("2026/1/26 · 遊樂場");
      fireEvent.click(screen.getByRole("button", { name: "存成相簿（3 張）" }));
      await waitFor(() => expect(s.createAlbum).toHaveBeenCalledWith("2026-1-26 · 遊樂場"));
      expect(await screen.findByText("已存成相簿「2026-1-26 · 遊樂場」")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("one photo into the next (audit round 116)", () => {
  it("[VIEW-073] 回歸：播放中換下一張時，上一張留在下面淡出（交叉淡化）；沒在播放時沒有（驗證第 116 輪：每張從黑色浮出）", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const tick = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
    try {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay title="足球" />
        </QueryClientProvider>,
      );
      await screen.findByRole("button", { name: "停止幻燈片" });
      expect(screen.queryByTestId("story-prev")).toBeNull(); // the first photo has none before it
      await tick(4000);
      await waitFor(() => expect(screen.getByTestId("lightbox-position").textContent).toBe("第 2 張，共 3 張"));
      const prev = await screen.findByTestId("story-prev");
      expect(prev.getAttribute("src")).toContain("p0");
      for (const img of screen.getByTestId("lightbox-stage").querySelectorAll("img:not(.story-prev)")) fireEvent.load(img); // the next one came (round 117)
      await tick(800); // and faded in over it
      expect(screen.queryByTestId("story-prev")).toBeNull(); // gone once it has faded
      fireEvent.click(screen.getByRole("button", { name: "停止幻燈片" }));
      fireEvent.keyDown(screen.getByTestId("lightbox"), { key: "ArrowRight" });
      await waitFor(() => expect(screen.getByTestId("lightbox-position").textContent).toBe("第 3 張，共 3 張"));
      expect(screen.queryByTestId("story-prev")).toBeNull(); // by hand: the photo just changes
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("the end card holds still, and a fade has no black frame (audit round 117)", () => {
  it("[VIEW-074] 回歸：結尾卡只在出現時把焦點放到「再播一次」，之後重新繪製不會把焦點拉回去（驗證第 117 輪：每 5 秒被拉回一次）", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const tick = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
    try {
      const ui = (n: number) => (
        <QueryClientProvider client={new QueryClient()}>
          <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay title="足球" seeAll={{ n, go: vi.fn() }} />
        </QueryClientProvider>
      );
      const { rerender } = render(ui(17));
      await screen.findByRole("button", { name: "停止幻燈片" });
      for (let i = 0; i < 3; i++) await tick(4000);
      const again = await screen.findByRole("button", { name: "再播一次" });
      expect(document.activeElement).toBe(again); // as it appears
      const keepBtn = screen.getByRole("button", { name: "存成相簿（3 張）" });
      keepBtn.focus();
      rerender(ui(18)); // a refetch behind it draws the card again
      await tick(100);
      expect(document.activeElement).toBe(keepBtn);
    } finally {
      vi.useRealTimers();
    }
  });

  it("[VIEW-075] 回歸：上一張在下一張載入前不淡出（沒有黑色那一格），維持推近後的大小（沒有縮回的跳動）；存成相簿後可「打開相簿」（驗證第 117 輪）", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const tick = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
    try {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay title="足球" />
        </QueryClientProvider>,
      );
      await screen.findByRole("button", { name: "停止幻燈片" });
      await tick(4000);
      const prev = await screen.findByTestId("story-prev");
      expect(prev.style.scale).toBe("1.05"); // where its drift ended
      await tick(1500); // the next has not loaded: the one before stays, whole
      expect(screen.getByTestId("story-prev")).toBe(prev);
      for (const img of screen.getByTestId("lightbox-stage").querySelectorAll("img:not(.story-prev)")) fireEvent.load(img);
      await tick(800);
      expect(screen.queryByTestId("story-prev")).toBeNull();
      // to the end, keep it, and open the album
      await tick(4000);
      await tick(4000);
      fireEvent.click(await screen.findByRole("button", { name: "存成相簿（3 張）" }));
      expect(await screen.findByText("打開相簿")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("the photo before goes softly; keeping a story keeps focus (audit round 118)", () => {
  it("[VIEW-076] 回歸：下一張載入後，上一張才開始淡出（class out），不是 0.8 秒後一格消失；上一張在新的一張出現的同一次繪製就在（驗證第 118 輪：邊緣露出後一格消失、2/33 黑格）", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const tick = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
    try {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay title="足球" />
        </QueryClientProvider>,
      );
      await screen.findByRole("button", { name: "停止幻燈片" });
      await tick(4000);
      const prev = await screen.findByTestId("story-prev");
      expect(prev.className).not.toContain("out"); // whole while the next loads
      for (const img of screen.getByTestId("lightbox-stage").querySelectorAll("img:not(.story-prev)")) fireEvent.load(img);
      await waitFor(() => expect(screen.getByTestId("story-prev").className).toContain("out")); // then it fades
    } finally {
      vi.useRealTimers();
    }
  });

  it("[VIEW-077] 回歸：用鍵盤按「存成相簿」後焦點留在結尾卡裡（驗證第 118 輪：按鈕消失，焦點掉到頁面）", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const tick = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
    try {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <Lightbox start={0} grid={grid} onClose={vi.fn()} autoplay title="足球" />
        </QueryClientProvider>,
      );
      await screen.findByRole("button", { name: "停止幻燈片" });
      for (let i = 0; i < 3; i++) await tick(4000);
      const keepBtn = await screen.findByRole("button", { name: "存成相簿（3 張）" });
      keepBtn.focus();
      fireEvent.click(keepBtn);
      await screen.findByText("打開相簿");
      await tick(50);
      expect(screen.getByTestId("lightbox").contains(document.activeElement)).toBe(true);
      expect(document.activeElement).not.toBe(screen.getByTestId("lightbox")); // on something in the card
    } finally {
      vi.useRealTimers();
    }
  });
});
