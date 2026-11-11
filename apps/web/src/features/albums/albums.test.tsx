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
  },
}));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => vi.fn(),
}));

import { AddToAlbum, FavoriteButton, typingIn } from "./AlbumButtons";
import { AlbumPage } from "./AlbumsPage";

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

