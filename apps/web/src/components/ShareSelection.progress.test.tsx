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
});
