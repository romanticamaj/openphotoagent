import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";
import i18n from "../../lib/i18n";
import { PreviewProgress } from "./PreviewProgress";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

describe("a running preview", () => {
  it("[ORG-013] 回歸：預覽進行中說做到哪、經過多久、約剩多久（驗證第 16 輪：60 秒什麼都沒有）", () => {
    const { rerender } = render(<PreviewProgress status="running" progress={{ done: 0, total: 0, elapsed_s: 1 }} hasEstimate={false} />);
    expect(screen.getByRole("status").textContent).toContain("正在讀取資料夾");
    rerender(<PreviewProgress status="running" progress={{ done: 0, total: 10, elapsed_s: 3 }} hasEstimate={false} />);
    expect(screen.getByRole("status").textContent).toContain("0／10");
    expect(screen.getByRole("status").textContent).toContain("3 秒");
    rerender(<PreviewProgress status="running" progress={{ done: 4, total: 10, elapsed_s: 20 }} hasEstimate={false} />);
    expect(screen.getByRole("status").textContent).toContain("約剩 30 秒"); // 20 s for 4, 6 to go
    rerender(<PreviewProgress status="ready" progress={{ done: 10, total: 10, elapsed_s: 50 }} hasEstimate={false} />);
    expect(screen.getByRole("status").textContent).toContain("正在估算全庫分布");
    rerender(<PreviewProgress status="ready" progress={{ done: 10, total: 10, elapsed_s: 52 }} hasEstimate={true} />);
    expect(screen.queryByRole("status")).toBeNull();
  });
});
