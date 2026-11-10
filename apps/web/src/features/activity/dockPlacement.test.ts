import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("where the progress card sits", () => {
  it("[ACT-010] 回歸：進度卡在側欄裡、導覽下方，不蓋住「設定」（驗證第 19 輪）", () => {
    const css = readFileSync(`${process.cwd()}/src/styles.css`, "utf-8");
    const router = readFileSync(`${process.cwd()}/src/router.tsx`, "utf-8");
    expect(css).toMatch(/\.sidebar \.progress-dock\s*\{[^}]*position:\s*static/);
    expect(css).toMatch(/\.sidebar\s*\{[^}]*flex-direction:\s*column/);
    // the nav and the card share one sidebar column
    expect(router).toMatch(/<div className="sidebar">[\s\S]*<nav className="sidenav"[\s\S]*<ProgressDock \/>[\s\S]*<\/div>\s*<main/);
  });
});
