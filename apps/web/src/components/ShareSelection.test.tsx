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
    expect(status.textContent).toContain("已存 3 張到 D:\\分享 2026-11-14（3 張）");
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
});
