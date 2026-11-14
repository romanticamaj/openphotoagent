import { describe, expect, it } from "vitest";
import i18n from "../../lib/i18n";
import { chipLabel } from "./AskPanel";

describe("the date chip (audit round 47)", () => {
  it("[ASK-DATE-002] 回歸：日期標籤寫最後一天，與理解問題那一行一致（寫成 -11-16，那行是 -11-15）", async () => {
    await i18n.changeLanguage("zh-TW");
    const t = i18n.getFixedT("zh-TW");
    const label = chipLabel(t, "taken_at", { from: "2025-11-09", to: "2025-11-16" });
    expect(label).toContain("2025-11-09");
    expect(label).toContain("2025-11-15");
    expect(label).not.toContain("2025-11-16");
  });
});
