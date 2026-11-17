import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";
import { ActivityCenter } from "./ActivityCenter";

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

  it("[ACT-023] 回歸：一個計畫只佔一列（驗證第 131 輪：兩個計畫顯示成四列，「建立整理計畫」與工作各一列）", () => {
    const job = { id: "j1", type: "job.label", status: "succeeded", subject: { kind: "job", id: "j1", job_kind: "organize", job_status: "awaiting_approval", plan_id: "p1", photos: 2 }, finished_at: "2026-11-17T22:37:00+08:00" };
    const plan = { id: "p1:plan", type: "output.plan", status: "succeeded", subject: { kind: "plan", id: "p1" }, finished_at: "2026-11-17T22:37:00+08:00" };
    const other = { id: "p2:plan", type: "output.plan", status: "succeeded", subject: { kind: "plan", id: "p2" }, finished_at: "2026-11-17T22:30:00+08:00" };
    render(<ActivityCenter open ops={[]} recent={[job, plan, other]} onClose={() => {}} />);
    expect(screen.getByRole("list", { name: "最近完成" }).querySelectorAll("li")).toHaveLength(2);
  });
});
