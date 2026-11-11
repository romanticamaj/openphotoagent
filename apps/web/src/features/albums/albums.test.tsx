import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";
import { navSections } from "../../lib/nav";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const s = vi.hoisted(() => ({
  fav: false,
  setFavorite: vi.fn(),
  albums: vi.fn(),
  addToAlbum: vi.fn(),
  createAlbum: vi.fn(),
  album: vi.fn(),
  removeFromAlbum: vi.fn(),
  deleteAlbum: vi.fn(),
  removeManyFromAlbum: vi.fn(),
}));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    asset: async () => ({ id: "a1", favorite: s.fav, albums: [] }),
    setFavorite: s.setFavorite,
    albums: s.albums,
    addToAlbum: s.addToAlbum,
    createAlbum: s.createAlbum,
    album: s.album,
    removeFromAlbum: s.removeFromAlbum,
    deleteAlbum: s.deleteAlbum,
    removeManyFromAlbum: s.removeManyFromAlbum,
  },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => vi.fn(),
}));

import { AddToAlbum, FavoriteButton, FavoriteSelection, typingIn, useFavorite } from "./AlbumButtons";
import { AlbumPage, AlbumsPage } from "./AlbumsPage";

const wrap = (el: React.ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>);

describe("favourites and albums (ADR-0041)", () => {
  it("[COL-005] 側欄有「相簿」", () => {
    expect(navSections(false)[0].items.map((i) => i.to)).toContain("/albums");
  });

  it("[COL-006] ★ 收藏與取消收藏（驗證第 26 輪：沒有收藏）", async () => {
    s.fav = false;
    s.setFavorite.mockResolvedValue({ favorite: true });
    wrap(<FavoriteButton assetId="a1" />);
    const b = await screen.findByRole("button", { name: "加入收藏" });
    expect(b.getAttribute("aria-pressed")).toBe("false");
    s.fav = true;
    fireEvent.click(b);
    await waitFor(() => expect(s.setFavorite).toHaveBeenCalledWith("a1", true));
    expect((await screen.findByRole("button", { name: "取消收藏" })).getAttribute("aria-pressed")).toBe("true");
  });

  it("[COL-007] 加入相簿：選現有的或新增一個", async () => {
    s.albums.mockResolvedValue({ albums: [{ id: "fav-default", kind: "favorites", name: "收藏", count: 1, cover: null }, { id: "b1", kind: "album", name: "墾丁", count: 3, cover: null }] });
    s.addToAlbum.mockResolvedValue({ count: 4 });
    s.createAlbum.mockResolvedValue({ id: "b2", name: "生日" });
    wrap(<AddToAlbum assetIds={["a1"]} />);
    fireEvent.click(screen.getByRole("button", { name: "加入相簿" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /墾丁/ }));
    await waitFor(() => expect(s.addToAlbum).toHaveBeenCalledWith("b1", ["a1"]));
    expect((await screen.findByRole("status")).textContent).toContain("已加入「墾丁」");
    fireEvent.click(screen.getByRole("button", { name: "加入相簿" }));
    expect(screen.queryByRole("menuitem", { name: /^收藏/ })).toBeNull(); // ★ is the favourite, not a menu row
    fireEvent.change(await screen.findByLabelText("新相簿名稱"), { target: { value: "生日" } });
    fireEvent.click(screen.getByRole("button", { name: "建立並加入" }));
    await waitFor(() => expect(s.createAlbum).toHaveBeenCalledWith("生日"));
    await waitFor(() => expect(s.addToAlbum).toHaveBeenCalledWith("b2", ["a1"]));
  });

  it("[COL-008] 回歸：在輸入框打字時，Lightbox 的快捷鍵不作用（「問這張」打 i 會關掉資訊面板）", () => {
    const input = document.createElement("input");
    const area = document.createElement("textarea");
    const div = document.createElement("div");
    expect(typingIn(input)).toBe(true);
    expect(typingIn(area)).toBe(true);
    expect(typingIn(document.createElement("select"))).toBe(true);
    expect(typingIn(div)).toBe(false);
  });
});

describe("album page and menu (audit round 33)", () => {
  it("[COL-009] 回歸：Esc 只關閉加入相簿的選單，不關掉 Lightbox", async () => {
    s.albums.mockResolvedValue({ albums: [] });
    const outer = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <div onKeyDown={outer}>
          <AddToAlbum assetIds={["a1"]} />
        </div>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "加入相簿" }));
    fireEvent.keyDown(await screen.findByRole("menu"), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(outer).not.toHaveBeenCalled();
  });

  it("[COL-010] 回歸：刪除相簿先確認；移除照片可以復原；照片格不疊在一起（驗證第 33 輪）", async () => {
    s.album.mockResolvedValue({ id: "b1", name: "墾丁", kind: "album", photos: [{ asset_id: "p1", ch: null, taken_at: null }, { asset_id: "p2", ch: null, taken_at: null }] });
    s.removeFromAlbum.mockResolvedValue({ count: 1 });
    s.addToAlbum.mockResolvedValue({ count: 2 });
    wrap(<AlbumPage albumId="b1" />);
    const tiles = await screen.findAllByRole("listitem");
    expect(tiles).toHaveLength(2);
    expect(tiles.every((t) => !t.classList.contains("tile"))).toBe(true); // .tile is the timeline's absolute tile
    fireEvent.click(screen.getAllByRole("button", { name: "從相簿移除" })[0]);
    await waitFor(() => expect(s.removeFromAlbum).toHaveBeenCalledWith("b1", "p1"));
    fireEvent.click(await screen.findByRole("button", { name: "復原" }));
    await waitFor(() => expect(s.addToAlbum).toHaveBeenCalledWith("b1", ["p1"]));
    fireEvent.click(screen.getByRole("button", { name: "刪除相簿" }));
    expect(s.deleteAlbum).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog").textContent).toContain("照片不會被刪除");
    fireEvent.click(screen.getByRole("button", { name: "確定刪除" }));
    await waitFor(() => expect(s.deleteAlbum).toHaveBeenCalledWith("b1"));
  });
});

describe("keys in the Lightbox (audit round 34)", () => {
  it("[COL-011] 回歸：選單用滑鼠打開時，Esc 也只關選單", async () => {
    s.albums.mockResolvedValue({ albums: [] });
    const outer = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <div onKeyDown={outer}>
          <AddToAlbum assetIds={["a1"]} />
        </div>
      </QueryClientProvider>,
    );
    const opener = screen.getByRole("button", { name: "加入相簿" });
    fireEvent.click(opener);
    await screen.findByRole("menu");
    fireEvent.keyDown(opener, { key: "Escape" }); // focus is still on the button it was opened with
    expect(screen.queryByRole("menu")).toBeNull();
    expect(outer).not.toHaveBeenCalled();
  });

  it("[COL-012] 回歸：F 在換照片後仍然有效（驗證第 34 輪：只有第一次有效）", async () => {
    s.setFavorite.mockReset();
    s.setFavorite.mockResolvedValue({ favorite: true });
    s.fav = false;
    function Probe({ id }: { id: string }) {
      const f = useFavorite(id);
      return (
        <button type="button" aria-disabled={!f.ready} onClick={f.toggle}>
          {id}
        </button>
      );
    }
    const qc = new QueryClient();
    const { rerender } = render(
      <QueryClientProvider client={qc}>
        <Probe id="a1" />
      </QueryClientProvider>,
    );
    const b1 = await screen.findByRole("button", { name: "a1" });
    await waitFor(() => expect(b1.getAttribute("aria-disabled")).toBe("false"));
    fireEvent.click(b1);
    await waitFor(() => expect(s.setFavorite).toHaveBeenCalledWith("a1", true));
    rerender(
      <QueryClientProvider client={qc}>
        <Probe id="a2" />
      </QueryClientProvider>,
    );
    const b2 = await screen.findByRole("button", { name: "a2" });
    await waitFor(() => expect(b2.getAttribute("aria-disabled")).toBe("false"));
    fireEvent.click(b2);
    await waitFor(() => expect(s.setFavorite).toHaveBeenCalledWith("a2", expect.any(Boolean)));
  });

  it("[COL-015] 多選照片一次加入收藏或相簿（驗證第 49 輪：選了 4 張卻只能整理或加標籤）", async () => {
    s.albums.mockResolvedValue({ albums: [{ id: "b1", kind: "album", name: "墾丁", count: 3, cover: null }] });
    s.addToAlbum.mockClear();
    const put = vi.fn().mockResolvedValue({ count: 7 });
    wrap(
      <>
        <FavoriteSelection put={put} />
        <AddToAlbum put={put} />
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "加入收藏" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith("fav-default"));
    fireEvent.click(screen.getByRole("button", { name: "加入相簿" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /墾丁/ }));
    await waitFor(() => expect(put).toHaveBeenCalledWith("b1"));
    expect(s.addToAlbum).not.toHaveBeenCalled(); // the selection goes in at once, not photo by photo
  });

  it("[COL-016] 回歸：相簿裡的照片有名稱（驗證第 50 輪：每張都叫「開啟照片」）", async () => {
    s.album.mockResolvedValue({ id: "b1", name: "墾丁", kind: "album", photos: [{ asset_id: "p1", ch: null, taken_at: "2024-07-01T10:00:00Z" }, { asset_id: "p2", ch: null, taken_at: null }] });
    wrap(<AlbumPage albumId="b1" />);
    expect(await screen.findByRole("button", { name: "照片，2024-07-01" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "照片，未知日期" })).toBeTruthy();
  });

  it("[COL-018] 回歸：批次加入後可以復原，只拿掉這次加入的（驗證第 50 輪：沒有復原）", async () => {
    s.albums.mockResolvedValue({ albums: [{ id: "b1", kind: "album", name: "墾丁", count: 3, cover: null }] });
    s.removeManyFromAlbum.mockResolvedValue({ count: 1 });
    const put = vi.fn().mockResolvedValue({ count: 3, added: ["p2", "p3"] });
    wrap(
      <>
        <FavoriteSelection put={put} />
        <AddToAlbum put={put} />
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "加入收藏" }));
    fireEvent.click(await screen.findByRole("button", { name: "復原" }));
    await waitFor(() => expect(s.removeManyFromAlbum).toHaveBeenCalledWith("fav-default", ["p2", "p3"]));
    fireEvent.click(screen.getByRole("button", { name: "加入相簿" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /墾丁/ }));
    fireEvent.click(await screen.findByRole("button", { name: "復原" }));
    await waitFor(() => expect(s.removeManyFromAlbum).toHaveBeenCalledWith("b1", ["p2", "p3"]));
  });

  it("[COL-019] 回歸：加入相簿的選單打開就能用方向鍵，靠左時往右開（驗證第 51 輪：被側欄切掉、↓ 沒反應）", async () => {
    s.albums.mockResolvedValue({ albums: [{ id: "b1", kind: "album", name: "墾丁", count: 3, cover: null }, { id: "b2", kind: "album", name: "生日", count: 1, cover: null }] });
    wrap(<AddToAlbum assetIds={["a1"]} />);
    fireEvent.click(screen.getByRole("button", { name: "加入相簿" }));
    const first = await screen.findByRole("menuitem", { name: /墾丁/ });
    await waitFor(() => expect(document.activeElement).toBe(first));
    const menu = screen.getByRole("menu");
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: /生日/ }));
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(screen.getByLabelText("新相簿名稱"));
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(first); // round again
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(document.activeElement).toBe(screen.getByLabelText("新相簿名稱"));
    expect(menu.classList.contains("album-menu-start")).toBe(true); // the button is at the left edge here
  });

  it("[COL-020] 回歸：選單預設往右開，只有右邊放不下時才往左（驗證第 52 輪：在 x≈530 的按鈕往左開，被側欄蓋住）", async () => {
    s.albums.mockResolvedValue({ albums: [] });
    const at = (left: number) => vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ left, right: left + 32, top: 0, bottom: 32, width: 32, height: 32, x: left, y: 0, toJSON: () => ({}) } as DOMRect);
    const { unmount } = wrap(<AddToAlbum assetIds={["a1"]} />);
    const spy = at(530); // the timeline's batch bar, right of a 300 px sidebar
    fireEvent.click(screen.getByRole("button", { name: "加入相簿" }));
    expect((await screen.findByRole("menu")).classList.contains("album-menu-start")).toBe(true);
    spy.mockRestore();
    unmount();
    wrap(<AddToAlbum assetIds={["a1"]} />);
    const spy2 = at(window.innerWidth - 40); // the Lightbox's top bar, at the right edge
    fireEvent.click(screen.getByRole("button", { name: "加入相簿" }));
    expect((await screen.findByRole("menu")).classList.contains("album-menu-start")).toBe(false);
    spy2.mockRestore();
  });

  it("[COL-022] 回歸：上千張的批次加入也能復原，失敗時會說（驗證第 53 輪：841 張復原時 422，沒有任何提示）", async () => {
    s.removeManyFromAlbum.mockReset();
    s.removeManyFromAlbum.mockResolvedValue({ count: 0 });
    const added = Array.from({ length: 1200 }, (_, i) => `p${i}`);
    const put = vi.fn().mockResolvedValue({ count: 1200, added });
    const { unmount } = wrap(<FavoriteSelection put={put} />);
    fireEvent.click(screen.getByRole("button", { name: "加入收藏" }));
    fireEvent.click(await screen.findByRole("button", { name: "復原" }));
    await waitFor(() => expect(s.removeManyFromAlbum).toHaveBeenCalledTimes(3));
    const sent = s.removeManyFromAlbum.mock.calls.map((c) => c[1] as string[]);
    expect(sent.every((ids) => ids.length <= 500)).toBe(true);
    expect(sent.flat()).toEqual(added);
    unmount();
    s.removeManyFromAlbum.mockRejectedValue(new Error("HTTP 500"));
    wrap(<FavoriteSelection put={put} />);
    fireEvent.click(screen.getByRole("button", { name: "加入收藏" }));
    fireEvent.click(await screen.findByRole("button", { name: "復原" }));
    expect((await screen.findByRole("alert")).textContent).toContain("沒有復原");
  });

  it("[COL-024] 回歸：按「改名」焦點進到名稱欄；空白名稱會說要輸入（驗證第 54 輪：打字沒反應、按了沒反應）", async () => {
    s.album.mockResolvedValue({ id: "b1", name: "墾丁", kind: "album", photos: [] });
    const { unmount } = wrap(<AlbumPage albumId="b1" />);
    fireEvent.click(await screen.findByRole("button", { name: "改名" }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "改名" })));
    fireEvent.change(screen.getByRole("textbox", { name: "改名" }), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "儲存" }));
    expect((await screen.findByRole("alert")).textContent).toContain("請輸入相簿名稱");
    fireEvent.keyDown(screen.getByRole("textbox", { name: "改名" }), { key: "Escape" });
    expect(screen.queryByRole("textbox", { name: "改名" })).toBeNull();
    unmount();
    s.albums.mockResolvedValue({ albums: [] });
    wrap(<AlbumsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "新增相簿" }));
    expect((await screen.findByRole("alert")).textContent).toContain("請輸入相簿名稱");
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "新相簿名稱" }));
  });

  it("[COL-025] 回歸：復原之後說「已復原 N 張」（驗證第 54 輪：提示就消失了，不知道有沒有成功）", async () => {
    s.removeManyFromAlbum.mockReset();
    s.removeManyFromAlbum.mockResolvedValue({ count: 0 });
    const put = vi.fn().mockResolvedValue({ count: 3, added: ["p1", "p2", "p3"] });
    wrap(<FavoriteSelection put={put} />);
    fireEvent.click(screen.getByRole("button", { name: "加入收藏" }));
    fireEvent.click(await screen.findByRole("button", { name: "復原" }));
    expect((await screen.findByText("已復原 3 張")).getAttribute("role")).toBe("status");
  });
});

