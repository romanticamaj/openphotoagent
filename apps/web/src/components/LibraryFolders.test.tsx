import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});
const TWO = [
  { id: "s1", root_alias: "photos", sub_path: "", photos: 80, analysed: 80 },
  { id: "s2", root_alias: "testdata", sub_path: "第19周", photos: 73, analysed: 40 },
];
const st = vi.hoisted(() => ({ rows: [] as unknown[], disk: null as null | { photos: number } }));
vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  api: {
    sources: async () => st.rows,
    folderPreview: async () => {
      if (!st.disk) throw new Error("no preview");
      return st.disk;
    },
  },
}));

import { LibraryFolders } from "./LibraryFolders";

describe("folders already in the library", () => {
  it("[IMP-033] 每個資料夾看得到張數與分析進度，按鈕交給呼叫端", async () => {
    st.rows = TWO;
    const act = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <LibraryFolders action={(s) => (s.analysed < s.photos ? "分析剩下的照片" : "重新分析")} onAct={act} />
      </QueryClientProvider>,
    );
    const row = (await screen.findByText("第19周")).closest("li")!;
    expect(row.textContent).toContain("73");
    expect(row.textContent).toContain("40");
    fireEvent.click(screen.getByRole("button", { name: "分析剩下的照片" }));
    expect(act).toHaveBeenCalledWith(expect.objectContaining({ id: "s2" }));
    expect(screen.getByRole("button", { name: "重新分析" })).toBeTruthy();
  });

  it("[IMP-036] 回歸：資料夾的張數包含收在裡面的子資料夾（驗證第 19 輪：photos 還寫 80 張）", async () => {
    st.rows = [
      { id: "r", root_alias: "photos", sub_path: "", photos: 80, analysed: 80, tree_photos: 120, tree_analysed: 80 },
      { id: "n", root_alias: "photos", sub_path: "新批次", photos: 40, analysed: 0 },
    ];
    const act = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <LibraryFolders action={(s) => (s.analysed < s.photos ? "分析剩下的照片" : "重新分析")} onAct={act} />
      </QueryClientProvider>,
    );
    const items = await screen.findAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(items[0].textContent).toContain("120");
    fireEvent.click(screen.getByRole("button", { name: "分析剩下的照片" })); // 40 of 120 are new
    expect(act).toHaveBeenCalledWith(expect.objectContaining({ id: "r" }));
  });

  it("[IMP-037] 回歸：資料夾裡有還沒加入的新照片時，卡片說有幾張，按鈕是分析剩下的（驗證第 20 輪）", async () => {
    st.rows = [{ id: "r", root_alias: "photos", sub_path: "", photos: 80, analysed: 80, tree_photos: 120, tree_analysed: 120 }];
    st.disk = { photos: 160 };
    const act = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <LibraryFolders action={(s) => (s.analysed < s.photos ? "分析剩下的照片" : "重新分析")} onAct={act} />
      </QueryClientProvider>,
    );
    expect((await screen.findByTestId("folder-new")).textContent).toContain("40");
    fireEvent.click(screen.getByRole("button", { name: "分析剩下的照片" }));
    expect(act).toHaveBeenCalledWith(expect.objectContaining({ id: "r" }));
    st.disk = null;
  });

  it("[IMP-034] 回歸：資料夾裡的子資料夾不另列一次（驗證第 2 輪：第8周 492 旁邊又有 0409 156）", async () => {
    st.rows = [
      ...TWO,
      { id: "s3", root_alias: "testdata", sub_path: "第8周", photos: 492, analysed: 492 },
      { id: "s4", root_alias: "testdata", sub_path: "第8周/0409", photos: 156, analysed: 156 },
    ];
    render(
      <QueryClientProvider client={new QueryClient()}>
        <LibraryFolders action={() => "整理"} onAct={vi.fn()} />
      </QueryClientProvider>,
    );
    await screen.findByText("第8周");
    expect(screen.queryByText("0409")).toBeNull();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });
});

