import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ActivityCenter } from "./ActivityCenter";
import { ActivityButton } from "./ActivityHost";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

describe("what just happened", () => {
  it("[ACT-008] 回歸：做完的工作留在活動中心的「最近完成」（驗證第 3 輪：分析完就什麼都看不到）", () => {
    const done = { id: "o1", type: "job.label", status: "succeeded", subject: { kind: "job", id: "j", job_kind: "label" }, finished_at: "2026-11-10T09:00:00+08:00" };
    const failed = { id: "o2", type: "model.download", status: "failed", finished_at: "2026-11-10T08:00:00+08:00" };
    render(<ActivityCenter open ops={[]} recent={[done, failed]} onClose={() => {}} />);
    expect(screen.getByText("目前沒有進行中的工作")).toBeTruthy();
    const list = screen.getByRole("list", { name: "最近完成" });
    expect(list.textContent).toContain("AI 分析");
    expect(list.querySelectorAll("li")).toHaveLength(2);
  });

  it("[ACT-008] 群組標題不顯示「control」這種內部代號", () => {
    const op = { id: "o3", type: "search.reindex", status: "running", lane: "control", worker: null };
    render(<ActivityCenter open ops={[op] as never} onClose={() => {}} />);
    expect(screen.queryByText("control")).toBeNull();
  });

  it("[ACT-022] 回歸：計畫那一列寫出幾張、到哪裡，按下就開啟那個計畫（驗證第 131 輪：只寫「建立整理計畫 · 完成」，也點不開）", () => {
    const job = { id: "j1", type: "job.label", status: "succeeded", subject: { kind: "job", id: "j1", job_kind: "organize", job_status: "completed", outcome: "discarded", plan_id: "p1", photos: 2, target: "sorted/picked" }, finished_at: "2026-11-17T22:37:00+08:00" };
    const open = vi.fn();
    render(<ActivityCenter open ops={[]} recent={[job]} onClose={() => {}} onOpenPlan={open} />);
    const row = screen.getByRole("button", { name: /2 張 → sorted\/picked/ });
    expect(row.textContent).toContain("已捨棄");
    fireEvent.click(row);
    expect(open).toHaveBeenCalledWith("p1");
  });

  it("[ACT-026] 回歸：計畫那一列寫出是哪一種計畫（驗證第 132～134 輪：複製到資料夾與依日期的列看起來一樣）", () => {
    const row = (id: string, plan_kind: string) => ({ id, type: "job.label", status: "succeeded", subject: { kind: "job", id, job_kind: "organize", job_status: "awaiting_approval", plan_id: `p-${id}`, photos: 2, plan_kind }, finished_at: "2026-11-17T22:37:00+08:00" });
    render(<ActivityCenter open ops={[]} recent={[row("j1", "selection"), row("j2", "by-date"), row("j3", "trash")]} onClose={() => {}} onOpenPlan={() => {}} />);
    const items = [...screen.getByRole("list", { name: "最近完成" }).querySelectorAll("li")].map((li) => li.textContent ?? "");
    expect(items[0]).toContain("複製到資料夾");
    expect(items[1]).toContain("依日期");
    expect(items[2]).toContain("移到垃圾桶");
  });

  it("[ACT-023] 回歸：一個計畫只佔一列（驗證第 131 輪：兩個計畫顯示成四列，「建立整理計畫」與工作各一列）", () => {
    const job = { id: "j1", type: "job.label", status: "succeeded", subject: { kind: "job", id: "j1", job_kind: "organize", job_status: "awaiting_approval", plan_id: "p1", photos: 2 }, finished_at: "2026-11-17T22:37:00+08:00" };
    const plan = { id: "p1:plan", type: "output.plan", status: "succeeded", subject: { kind: "plan", id: "p1" }, finished_at: "2026-11-17T22:37:00+08:00" };
    const other = { id: "p2:plan", type: "output.plan", status: "succeeded", subject: { kind: "plan", id: "p2" }, finished_at: "2026-11-17T22:30:00+08:00" };
    render(<ActivityCenter open ops={[]} recent={[job, plan, other]} onClose={() => {}} />);
    expect(screen.getByRole("list", { name: "最近完成" }).querySelectorAll("li")).toHaveLength(2);
  });
});

describe("the drawer from the keyboard", () => {
  it("[ACT-024] 回歸：打開活動時焦點進到抽屜，Tab 一下就到第一列；Esc 關閉後焦點回到「活動」（驗證第 132 輪：15 次 Tab，Esc 落到頁面）", () => {
    const job = { id: "j1", type: "job.label", status: "succeeded", subject: { kind: "job", id: "j1", job_kind: "organize", plan_id: "p1", photos: 2 }, finished_at: "2026-11-17T22:37:00+08:00" };
    const btn = document.createElement("button");
    btn.dataset.testid = "activity-button";
    document.body.appendChild(btn);
    btn.focus();
    const onClose = vi.fn();
    const { rerender } = render(<ActivityCenter open ops={[]} recent={[job]} onClose={onClose} onOpenPlan={() => {}} />);
    const drawer = screen.getByRole("dialog", { name: "活動" });
    expect(drawer.contains(document.activeElement)).toBe(true);
    const focusable = drawer.querySelectorAll("button, a[href], [tabindex]:not([tabindex='-1'])");
    expect(focusable[0]?.textContent).toContain("2 張"); // the first row is the first stop
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
    rerender(<ActivityCenter open={false} ops={[]} recent={[job]} onClose={onClose} onOpenPlan={() => {}} />);
    expect(document.activeElement).toBe(btn);
    btn.remove();
  });

  it("[ACT-025] 回歸：「活動」按鈕說出抽屜開著或關著（驗證第 132 輪：沒有 aria-expanded）", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, enabled: false } } });
    const { rerender } = render(<QueryClientProvider client={qc}><ActivityButton onToggle={() => {}} expanded={false} /></QueryClientProvider>);
    const b = screen.getByTestId("activity-button");
    expect(b.getAttribute("aria-expanded")).toBe("false");
    rerender(<QueryClientProvider client={qc}><ActivityButton onToggle={() => {}} expanded /></QueryClientProvider>);
    expect(b.getAttribute("aria-expanded")).toBe("true");
  });
});
