import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";
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
});
