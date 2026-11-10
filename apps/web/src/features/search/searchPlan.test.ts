import { describe, expect, it } from "vitest";
import i18n from "../../lib/i18n";
import { bodyFor, chipsFrom, countText, withoutPerson } from "./searchPlan";

const planner = (filters: Record<string, unknown>, explain: string[], text = "") => ({
  source: "rules",
  confidence: 0.6,
  explain,
  text,
  filters,
  unapplied: [],
});

describe("the search a planned query becomes", () => {
  it("[SRCH-UI-012] 回歸：輸入人名後，人物條件留在搜尋裡並成為可移除的標籤（驗證第 14 輪：「測試A」顯示整個圖庫）", () => {
    const chips = chipsFrom(planner({ persons_all: ["p1"] }, ["「測試A」→ 人物"], ""));
    expect(chips).toEqual([{ kind: "person", value: "測試A", id: "p1" }]);
    expect(bodyFor("測試A", chips, { text: "" })).toEqual({ q: null, filters: { persons_all: ["p1"] } });
    const withWords = chipsFrom(planner({ persons_all: ["p1"] }, ["「測試A」→ 人物"], "足球"));
    expect(bodyFor("測試A 足球", withWords, { text: "足球" })).toEqual({ q: "足球", filters: { persons_all: ["p1"] } });
    expect(bodyFor("測試A", [], { text: "" })).toEqual({ q: null, filters: {} }); // the chip removed: the filter goes too
  });

  it("[SRCH-UI-013] 回歸：拿掉人物標籤時，搜尋框也拿掉那個名字（驗證第 15 輪：框裡還寫著「測試A 足球」）", () => {
    expect(withoutPerson("測試A 足球", "測試A")).toBe("足球");
    expect(withoutPerson("測試a的照片", "測試A")).toBe("的照片");
    expect(withoutPerson("測試A", "測試A")).toBe("");
  });

  it("[SRCH-UI-012] 日期標籤照舊；還沒規劃時先請伺服器規劃", () => {
    const chips = chipsFrom(planner({ taken_at: { from: "2025-01-01", to: "2025-12-31" } }, ["「去年」→ 2025"], "海邊"));
    expect(chips[0]).toMatchObject({ kind: "range", from: "2025-01-01", to: "2025-12-31" });
    expect(bodyFor("去年 海邊", [], null)).toEqual({ q: "去年 海邊", plan: true, filters: {} });
  });

  it("[SRCH-UI-014] 回歸：結果被截在前 250 張時，說是「最相關的 250 張」（驗證第 18 輪：看起來像剛好 250 張）", async () => {
    await i18n.changeLanguage("zh-TW");
    const t = i18n.t.bind(i18n);
    expect(countText(t, 250, true, "zh-TW")).toBe("最相關的 250 張");
    expect(countText(t, 17, false, "zh-TW")).toBe("17 張");
  });
});
