import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../lib/i18n";

const s = vi.hoisted(() => ({ progress: null as null | ((done: number, total: number) => void), finish: null as null | (() => void) }));
vi.mock("../lib/shareMany", async (orig) => ({
  ...(await orig<typeof import("../lib/shareMany")>()),
  shareMany: (_ids: string[], _d: unknown, _day: string, onProgress?: (done: number, total: number) => void) =>
    new Promise((resolve) => {
      s.progress = onProgress ?? null;
      s.finish = () => resolve({ kind: "saved", handle: "h", dir: "D:\分享", n: 3, failed: 0 });
    }),
}));

import { ShareSelection } from "./ShareSelection";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

describe("a large share says how far it is", () => {
  it("[SHARE-033] 回歸：選資料夾時不寫「準備中」；選好後寫「準備中 k/n…」（驗證第 132 輪：大照片在「準備中…」停約 4 秒、看不到進度）", async () => {
    render(<ShareSelection ids={["a", "b", "c"]} open={async () => ""} />);
    const b = screen.getByRole("button", { name: "分享 3 張…" });
    fireEvent.click(b);
    expect(b.textContent).not.toContain("準備中"); // the folder dialog is up: nothing is being made yet
    act(() => s.progress?.(0, 3));
    expect(b.textContent).toContain("準備中 0/3…");
    act(() => s.progress?.(2, 3));
    expect(b.textContent).toContain("準備中 2/3…");
    await act(async () => s.finish?.());
    expect(b.textContent).toBe("分享 3 張…");
  });

  it("[SHARE-034] 回歸：「準備中 k/n…」不讓按鈕變寬：最寬的字一直在按鈕裡佔位（驗證第 133 輪：97 → 107 px，後面兩個按鈕移位）", async () => {
    render(<ShareSelection ids={["a", "b", "c"]} open={async () => ""} />);
    const b = screen.getByRole("button", { name: "分享 3 張…" });
    const holders = () => [...b.querySelectorAll("[data-hold]")].map((x) => x.getAttribute("data-hold"));
    const atRest = holders();
    expect(atRest).toContain("準備中 3/3…");
    fireEvent.click(b);
    act(() => s.progress?.(1, 3));
    expect(holders()).toEqual(atRest);
    const { readFileSync } = await import("node:fs");
    const css = readFileSync(`${process.cwd()}/src/styles.css`, "utf-8").replace(/\s+/g, " ");
    expect(css).toMatch(/\.share-hold::after \{ content: attr\(data-hold\); \}/);
    await act(async () => s.finish?.());
  });
});
