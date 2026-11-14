import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../lib/i18n";
import type { ManyResult } from "../lib/shareMany";
import { ShareSelection } from "./ShareSelection";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

describe("分享 N 張 on the selection bar (ADR-0054; audit round 124: one photo at a time only)", () => {
  it("[SHARE-021] 選了照片就能「分享 N 張」；存好後說存在哪裡、幾張失敗，並可再開啟資料夾", async () => {
    const run = vi.fn(async (): Promise<ManyResult> => ({ kind: "saved", handle: "h", dir: "D:\\分享 2026-11-14（3 張）", n: 3, failed: 1 }));
    const open = vi.fn(async () => "D:\\x");
    render(<ShareSelection ids={["a", "b", "c", "d"]} run={run} open={open} />);
    fireEvent.click(screen.getByRole("button", { name: "分享 4 張…" }));
    expect(run).toHaveBeenCalledWith(["a", "b", "c", "d"]);
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("已存 3 張到「分享 2026-11-14（3 張）」"); // its name, not its path (ADR-0054 addendum 2)
    expect(status.textContent).toContain("1 張無法分享");
    fireEvent.click(screen.getByRole("button", { name: "開啟資料夾" }));
    expect(open).toHaveBeenCalledWith("h");
  });

  it("[SHARE-022] 資料夾在相片庫裡時說原因；取消不說話；全選或超過上限時說為什麼不能分享", async () => {
    const run = vi.fn(async (): Promise<ManyResult> => Promise.reject(new Error("SHARE_IN_ROOT")));
    const { unmount } = render(<ShareSelection ids={["a"]} run={run} />);
    fireEvent.click(screen.getByRole("button", { name: "分享 1 張…" }));
    expect((await screen.findByRole("alert")).textContent).toContain("相片庫以外");
    unmount();
    const cancel = vi.fn(async (): Promise<ManyResult> => ({ kind: "cancelled" }));
    const r2 = render(<ShareSelection ids={["a"]} run={cancel} />);
    fireEvent.click(screen.getByRole("button", { name: "分享 1 張…" }));
    await vi.waitFor(() => expect(cancel).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    r2.unmount();
    render(<ShareSelection ids={null} run={run} />);
    const b = screen.getByRole("button", { name: /分享/ });
    expect(b.getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("note").textContent).toContain("200");
  });

  it("[SHARE-023] 回歸：殼以字串拒絕（Tauri 的錯誤不是 Error）時也說出相片庫的原因（驗證第 125 輪：顯示「分享失敗」）", async () => {
    const run = vi.fn(async (): Promise<ManyResult> => Promise.reject("SHARE_IN_ROOT"));
    render(<ShareSelection ids={["a"]} run={run} />);
    fireEvent.click(screen.getByRole("button", { name: "分享 1 張…" }));
    expect((await screen.findByRole("alert")).textContent).toContain("相片庫以外");
  });

  it("[SHARE-024] 回歸：全選時按鈕寫已選的張數，不寫「分享 0 張」（驗證第 125 輪：已選 845 張旁寫分享 0 張）", () => {
    render(<ShareSelection ids={null} count={845} />);
    expect(screen.getByRole("button", { name: "分享 845 張…" }).getAttribute("aria-disabled")).toBe("true");
    expect(screen.queryByRole("button", { name: /分享 0 張/ })).toBeNull();
  });

  it("[SHARE-025] 回歸：存好的訊息不塞進選取列，只寫資料夾名稱（驗證第 125 輪：完整路徑把垃圾桶與複製擠到第二行）", async () => {
    const run = vi.fn(async (): Promise<ManyResult> => ({ kind: "saved", handle: "h", dir: "C:\\t\\audit\\share125\\分享 2026-11-14（5 張）", n: 5, failed: 0 }));
    const { container } = render(
      <div data-testid="bar">
        <ShareSelection ids={["a", "b", "c", "d", "e"]} run={run} open={async () => ""} />
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: "分享 5 張…" }));
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("已存 5 張到「分享 2026-11-14（5 張）」");
    expect(status.textContent).not.toContain("share125");
    expect(container.querySelector("[data-testid=bar]")!.contains(status)).toBe(false);
  });
});

