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
const st = vi.hoisted(() => ({ rows: [] as unknown[] }));
vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  api: { sources: async () => st.rows },
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

