import { describe, expect, it } from "vitest";
import { navSections } from "./nav";

describe("navigation (ADR-0029)", () => {
  it("[IMP-013] 圖庫、工作、系統三組，匯入在工作的第一個", () => {
    const s = navSections(false);
    expect(s.map((g) => g.key)).toEqual(["nav.groupLibrary", "nav.groupWork", "nav.groupSystem"]);
    expect(s[0].items.map((i) => i.to)).toContain("/explore");
    expect(s[1].items[0].to).toBe("/import");
    expect(s[1].items.map((i) => i.to)).toContain("/organize");
  });

  it("[IMP-013] 人物只有開啟人臉後才出現（FACE-GATE-001）", () => {
    const has = (on: boolean) => navSections(on).flatMap((g) => g.items).some((i) => i.to === "/people");
    expect(has(false)).toBe(false);
    expect(has(true)).toBe(true);
  });
});

describe("匯入 and 整理 say what they do (audit rounds 55-56)", () => {
  it("[IMP-040] 回歸：側欄的「匯入」「整理」各有一行說明：加入圖庫 vs 複製到輸出資料夾", () => {
    const work = navSections(false)[1].items;
    expect(work.find((i) => i.to === "/import")?.hint).toBe("nav.importHint");
    expect(work.find((i) => i.to === "/organize")?.hint).toBe("nav.organizeHint");
  });
});
