import { describe, expect, it } from "vitest";
import { runningKey } from "./OrganizeWizard";

describe("what an organize says while it runs (audit round 73)", () => {
  it("[ORG-022] 回歸：依日期（不使用 AI）寫「正在建立計畫…」，不是「分析中…」", async () => {
    const zh = (await import("@opa/i18n/locales/zh-TW.json")).default as unknown as { organize: Record<string, string> };
    expect(runningKey({ params: { preset: "by-date" } })).toBe("organize.planning");
    expect(zh.organize.planning).toBe("正在建立計畫…");
    expect(runningKey({ params: { preset: "by-content-12" } })).toBe("organize.running");
    expect(zh.organize.running).toBe("分析中…");
  });
});
