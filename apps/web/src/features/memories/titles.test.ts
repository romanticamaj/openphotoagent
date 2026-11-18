import { beforeAll, describe, expect, it } from "vitest";
import i18n from "../../lib/i18n";
import { eventTitle, themeTitle } from "./titles";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

describe("memory titles (ADR-0049 addendum 8)", () => {
  it("[MEM-055] 回歸：今年、去年用說的；今年的活動不寫年份（驗證第 138 輪：標題只有「日期 · 主題」）", () => {
    const t = i18n.t.bind(i18n);
    expect(themeTitle(t, 2026, "足球", "2026-11-18")).toBe("今年的足球");
    expect(themeTitle(t, 2025, "足球", "2026-11-18")).toBe("去年的足球");
    expect(themeTitle(t, 2023, "足球", "2026-11-18")).toBe("2023 的足球");
    expect(themeTitle(t, 2025, "足球")).toBe("2025 的足球"); // no today: as before
    expect(eventTitle(t, "2026-06-22", null, "運動場", "2026-11-18")).toBe("6/22 · 運動場");
    expect(eventTitle(t, "2025-04-08", "2025-04-09", "手工", "2026-11-18")).toBe("2025/4/8–4/9 · 手工");
    expect(eventTitle(t, "2025-11-12", null, null, "2026-11-18")).toBe("2025/11/12");
  });
});
