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

  it("[SHARE-026] 回歸：存好後焦點到提示的「開啟資料夾」；在提示上按 Esc 只關提示、不清掉選取，焦點回到分享按鈕（驗證第 126 輪：Tab 到不了、Esc 後焦點掉了）", async () => {
    const run = vi.fn(async (): Promise<ManyResult> => ({ kind: "saved", handle: "h", dir: "D:\分享（2 張）", n: 2, failed: 0 }));
    const outer = vi.fn();
    render(
      <div onKeyDown={outer}>
        <ShareSelection ids={["a", "b"]} run={run} open={async () => ""} />
      </div>,
    );
    const share = screen.getByRole("button", { name: "分享 2 張…" });
    fireEvent.click(share);
    const openBtn = await screen.findByRole("button", { name: "開啟資料夾" });
    await vi.waitFor(() => expect(document.activeElement).toBe(openBtn));
    fireEvent.keyDown(openBtn, { key: "Escape" });
    expect(screen.queryByRole("status")).toBeNull();
    expect(outer).not.toHaveBeenCalled(); // the selection stays
    expect(document.activeElement).toBe(share);
  });

  it("[SHARE-027] 回歸：準備中與被拒絕時，選取列的按鈕不移位：不在按鈕下加說明列，拒絕原因也是浮動提示（驗證第 126 輪）", async () => {
    let fail: (e: unknown) => void = () => undefined;
    const run = vi.fn(() => new Promise<ManyResult>((_ok, no) => (fail = no)));
    const { container } = render(
      <div data-testid="bar">
        <ShareSelection ids={["a"]} run={run} />
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: "分享 1 張…" }));
    const busy = await screen.findByRole("button", { name: "準備中…" });
    expect(busy.getAttribute("aria-busy")).toBe("true");
    expect(screen.queryByRole("note")).toBeNull(); // no line under the button
    fail("SHARE_IN_ROOT");
    const alert = await screen.findByRole("alert");
    expect(container.querySelector("[data-testid=bar]")!.contains(alert)).toBe(false);
  });

  it("[SHARE-029] 回歸：準備中時按鈕保持原本的寬度：原本的字留在按鈕裡佔位（隱藏、不唸出）（驗證第 127 輪：按鈕縮 16 px，旁邊的按鈕滑動 3 秒）", async () => {
    const run = vi.fn(() => new Promise<ManyResult>(() => undefined));
    render(<ShareSelection ids={["a"]} run={run} />);
    fireEvent.click(screen.getByRole("button", { name: "分享 1 張…" }));
    const busy = await screen.findByRole("button", { name: "準備中…" });
    const holder = busy.querySelector("[aria-hidden=true]");
    expect(holder?.textContent).toBe("分享 1 張…"); // the label it had holds the width
    expect(holder?.className).toContain("invisible");
  });

  it("[SHARE-030] 回歸：在提示上按 Tab 在提示的按鈕間輪流，最後一個之後回到分享按鈕（驗證第 127 輪：Tab 跳到頁面頂端）", async () => {
    const run = vi.fn(async (): Promise<ManyResult> => ({ kind: "saved", handle: "h", dir: "D:\分享（2 張）", n: 2, failed: 0 }));
    render(<ShareSelection ids={["a", "b"]} run={run} open={async () => ""} />);
    const share = screen.getByRole("button", { name: "分享 2 張…" });
    fireEvent.click(share);
    const openBtn = await screen.findByRole("button", { name: "開啟資料夾" });
    const x = screen.getByRole("button", { name: "關閉" });
    await vi.waitFor(() => expect(document.activeElement).toBe(openBtn));
    x.focus();
    fireEvent.keyDown(x, { key: "Tab" });
    expect(document.activeElement).toBe(share);
    openBtn.focus();
    fireEvent.keyDown(openBtn, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(share);
    expect(screen.getByRole("status")).toBeTruthy(); // the toast stays
  });
});


describe("faces before a share (ADR-0049 addendum 8)", () => {
  it("[SHARE-037] 回歸：人臉功能開著時，分享前說出幾張有人臉，可以全部分享、只分享其他的或取消；關著時直接分享（驗證第 138～139 輪：14 個封面 13 個有人臉，分享時沒有任何提示）", async () => {
    const run = vi.fn(async (): Promise<ManyResult> => ({ kind: "saved", handle: "h", dir: "D:\\x", n: 1, failed: 0 }));
    const check = vi.fn(async () => ({ enabled: true, with_faces: ["a", "b"] }));
    const { unmount } = render(<ShareSelection ids={["a", "b", "c"]} run={run} open={async () => ""} check={check} />);
    fireEvent.click(screen.getByRole("button", { name: "分享 3 張…" }));
    const ask = await screen.findByRole("alertdialog");
    expect(ask.textContent).toContain("3 張中有 2 張有人臉");
    expect(run).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "只分享其他 1 張" }));
    await vi.waitFor(() => expect(run).toHaveBeenCalledWith(["c"]));
    unmount();
    const run2 = vi.fn(async (): Promise<ManyResult> => ({ kind: "cancelled" }));
    render(<ShareSelection ids={["a", "b", "c"]} run={run2} open={async () => ""} check={async () => ({ enabled: false })} />);
    fireEvent.click(screen.getByRole("button", { name: "分享 3 張…" }));
    await vi.waitFor(() => expect(run2).toHaveBeenCalledWith(["a", "b", "c"]));
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});

describe("faces before a share, safe by default", () => {
  it("[SHARE-038] 回歸：焦點先在較安全的選項，Tab 留在對話框裡；查不出來時先問，不直接分享（驗證第 141 輪：焦點在「全部分享」、Tab 跑出去、檢查失敗就直接分享）", async () => {
    const run = vi.fn(async (): Promise<ManyResult> => ({ kind: "cancelled" }));
    const { unmount } = render(<ShareSelection ids={["a", "b", "c"]} run={run} open={async () => ""} check={async () => ({ enabled: true, with_faces: ["a"] })} />);
    fireEvent.click(screen.getByRole("button", { name: "分享 3 張…" }));
    await screen.findByRole("alertdialog");
    const others = screen.getByRole("button", { name: "只分享其他 2 張" });
    await vi.waitFor(() => expect(document.activeElement).toBe(others));
    const cancel = screen.getByRole("button", { name: "取消" });
    cancel.focus();
    fireEvent.keyDown(cancel, { key: "Tab" });
    expect(screen.getByRole("alertdialog").contains(document.activeElement)).toBe(true); // round, not out
    unmount();
    const run2 = vi.fn(async (): Promise<ManyResult> => ({ kind: "cancelled" }));
    render(<ShareSelection ids={["a", "b"]} run={run2} open={async () => ""} check={async () => Promise.reject(new Error("down"))} />);
    fireEvent.click(screen.getByRole("button", { name: "分享 2 張…" }));
    const ask = await screen.findByRole("alertdialog");
    expect(ask.textContent).toContain("無法確認照片裡有沒有人臉");
    expect(run2).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "取消" })));
    fireEvent.click(screen.getByRole("button", { name: "仍要分享" }));
    await vi.waitFor(() => expect(run2).toHaveBeenCalledWith(["a", "b"]));
  });
});

describe("faces before a share, the risky choice last", () => {
  it("[SHARE-040] 回歸：「全部分享」排在最後並標成警示（驗證第 142 輪：排在第一個、看起來和其他一樣）", async () => {
    const run = vi.fn(async (): Promise<ManyResult> => ({ kind: "cancelled" }));
    render(<ShareSelection ids={["a", "b", "c"]} run={run} open={async () => ""} check={async () => ({ enabled: true, with_faces: ["a"] })} />);
    fireEvent.click(screen.getByRole("button", { name: "分享 3 張…" }));
    const dlg = await screen.findByRole("alertdialog");
    const names = [...dlg.querySelectorAll("button")].map((b) => b.textContent);
    expect(names[names.length - 1]).toBe("全部分享");
    expect(screen.getByRole("button", { name: "全部分享" }).className).toContain("danger");
  });
});

describe("faces before a share, a second press for all", () => {
  it("[SHARE-041] 回歸：「全部分享」要再按一次確認，一次 Shift+Tab＋Enter 不會分享有人臉的照片（驗證第 143 輪）", async () => {
    const run = vi.fn(async (): Promise<ManyResult> => ({ kind: "cancelled" }));
    render(<ShareSelection ids={["a", "b"]} run={run} open={async () => ""} check={async () => ({ enabled: true, with_faces: ["a"] })} />);
    fireEvent.click(screen.getByRole("button", { name: "分享 2 張…" }));
    await screen.findByRole("alertdialog");
    fireEvent.click(screen.getByRole("button", { name: "全部分享" }));
    expect(run).not.toHaveBeenCalled();
    const sure = screen.getByRole("button", { name: "確定分享有人臉的照片" });
    expect(document.activeElement).toBe(sure);
    // a moment later, as a person presses it (revised in round 144: a press within 600 ms is ignored, SHARE-042)
    const later = performance.now() + 1000;
    vi.spyOn(performance, "now").mockReturnValue(later);
    fireEvent.click(sure);
    await vi.waitFor(() => expect(run).toHaveBeenCalledWith(["a", "b"]));
    vi.restoreAllMocks();
  });
});

describe("faces before a share, not by a held key", () => {
  it("[SHARE-042] 回歸：「確定分享有人臉的照片」剛出現時不接受按鍵，連按兩次 Enter 不會分享（驗證第 144 輪）", async () => {
    let now = 1000;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const run = vi.fn(async (): Promise<ManyResult> => ({ kind: "cancelled" }));
    render(<ShareSelection ids={["a", "b"]} run={run} open={async () => ""} check={async () => ({ enabled: true, with_faces: ["a"] })} />);
    fireEvent.click(screen.getByRole("button", { name: "分享 2 張…" }));
    await screen.findByRole("alertdialog");
    fireEvent.click(screen.getByRole("button", { name: "全部分享" }));
    const sure = screen.getByRole("button", { name: "確定分享有人臉的照片" });
    now += 100; // the second Enter of a double press
    fireEvent.click(sure);
    expect(run).not.toHaveBeenCalled();
    now += 700;
    fireEvent.click(sure);
    await vi.waitFor(() => expect(run).toHaveBeenCalledWith(["a", "b"]));
    vi.restoreAllMocks();
  });
});
