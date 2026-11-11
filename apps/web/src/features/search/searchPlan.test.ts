import { describe, expect, it } from "vitest";
import i18n from "../../lib/i18n";
import { bodyFor, cappedNote, chipLabel, chipsFrom, countText, onlyByMeaning, softView, weakNeedsMore, weakTail, withoutPerson } from "./searchPlan";

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
    expect(countText(t, 250, true, "zh-TW")).toBe("250 張以上 · 依相關度排序"); // wording as of SRCH-UI-016
    expect(countText(t, 17, false, "zh-TW")).toBe("17 張");
  });

  it("[SRCH-UI-015] 回歸：「以文搜圖」只標在只靠影像意思找到的照片（驗證第 22 輪：每張都有）", () => {
    expect(onlyByMeaning(["image_vector"])).toBe(true);
    expect(onlyByMeaning(["image_vector", "caption_vector"])).toBe(true);
    expect(onlyByMeaning(["image_vector", "tag"])).toBe(false); // the word matched: nothing to explain
    expect(onlyByMeaning(["fulltext", "image_vector"])).toBe(false);
    expect(onlyByMeaning(["tag"])).toBe(false);
  });
});

describe("an expression that ranks (ADR-0032 addendum)", () => {
  const t = i18n.getFixedT("zh-TW");
  it("[SRCH-CNC-010] 有看得出的照片：分隔線在第 shown 張之後，按鈕加上表情標籤篩選", () => {
    const v = softView(t, [{ word: "開心", key: "mood.smile", shown: 2 }], 175, []);
    expect(v?.at).toBe(2);
    expect(v?.divider).toBe("以下 173 張沒看出「開心」，仍符合其他條件");
    expect(v?.only).toBe("只看開心（2 張）");
    expect(v?.chip).toEqual({ kind: "soft", value: "mood.smile", word: "開心" });
  });

  it("[SRCH-CNC-010] 沒有看得出的照片：一句說明、沒有分隔線也沒有按鈕", () => {
    const v = softView(t, [{ word: "開心", key: "mood.smile", shown: 0 }], 175, []);
    expect(v?.at).toBeNull();
    expect(v?.note).toBe("沒有看得出「開心」的照片，下面是符合其他條件的照片。");
    expect(v?.only).toBeNull();
  });

  it("[SRCH-CNC-010] 已經只看開心，或全部都看得出：不再分隔", () => {
    expect(softView(t, [{ word: "開心", key: "mood.smile", shown: 2 }], 2, [])).toBeNull();
    expect(softView(t, [{ word: "開心", key: "mood.smile", shown: 2 }], 175, [{ kind: "soft", value: "mood.smile", word: "開心" }])).toBeNull();
    expect(softView(t, [], 175, [])).toBeNull();
  });
});

describe("only the expression (SRCH-CNC-012)", () => {
  it("[SRCH-CNC-012] 回歸：「只看開心」仍是同一個查詢，只留看得出的照片（驗證第 24 輪：23 張變 19 張）", () => {
    const chips = [{ kind: "soft" as const, value: "mood.smile", word: "開心" }];
    expect(bodyFor("小孩踢球很開心", chips, { text: "小孩踢球很開心" })).toEqual({ q: "小孩踢球很開心", soft_only: true, filters: {} });
  });
});

describe("results cut at a route's limit (SRCH-UI-016)", () => {
  const t = i18n.getFixedT("zh-TW");
  it("[SRCH-UI-016] 回歸：被截斷時說「N 張以上」，看到最後時說其餘相似度較低（驗證第 25 輪：「最相關的 233 張」看不出還有更多）", () => {
    expect(countText(t, 233, true, "zh-TW")).toBe("233 張以上 · 依相關度排序");
    expect(cappedNote(t, 233, true, false)).toBe("只列出最相關的 233 張，其餘照片相似度較低。");
    expect(cappedNote(t, 233, true, true)).toBeNull(); // more pages still to load
    expect(cappedNote(t, 17, false, false)).toBeNull();
  });

  it("[SRCH-UI-017] 回歸：標籤的移除按鈕念出名稱，不念內部代號（驗證第 25 輪：「移除 mood.smile」）", () => {
    expect(chipLabel(t, { kind: "soft", value: "mood.smile", word: "開心" }, {})).toBe("只看開心");
    expect(chipLabel(t, { kind: "tag", value: "animal.dog" }, { "animal.dog": "狗" })).toBe("狗");
    expect(chipLabel(t, { kind: "person", value: "測試A", id: "p1" }, {})).toBe("人物：測試A");
  });
});

describe("weak look-alikes (SRCH-SEM-007)", () => {
  const t = i18n.getFixedT("zh-TW");
  it("[SRCH-SEM-007] 回歸：較不相關的照片先收起，按一下才顯示（驗證第 26 輪：最後 35 張大多不是踢球）", () => {
    expect(weakTail(t, 160, 73, false, 233)).toEqual({ from: 160, more: "顯示較不相關的 73 張", fetch: false });
    expect(weakTail(t, 160, 73, true, 233)).toBeNull(); // shown
    expect(weakTail(t, undefined, 0, false, 233)).toBeNull();
    expect(weakTail(t, 0, 5, false, 5)).toBeNull(); // nothing strong: show them, the list is not hidden whole
  });
});

describe("weak look-alikes across pages (SRCH-SEM-008)", () => {
  const t = i18n.getFixedT("zh-TW");
  it("[SRCH-SEM-008] 回歸：較不相關的照片還沒載入前不收起，照常「載入更多」（驗證第 27 輪：第 2 頁的好照片被收進按鈕）", () => {
    expect(weakTail(t, 186, 14, false, 100)).toBeNull(); // the strong ones are still coming
    expect(weakTail(t, 186, 14, false, 186)).toEqual({ from: 186, more: "顯示較不相關的 14 張", fetch: true }); // next page is them
    expect(weakTail(t, 186, 14, false, 200)).toEqual({ from: 186, more: "顯示較不相關的 14 張", fetch: false });
  });
});

describe("showing the weak look-alikes (SRCH-SEM-009)", () => {
  it("[SRCH-SEM-009] 回歸：按「顯示較不相關的 27 張」就載完 27 張，不必再按「載入更多」（驗證第 28 輪：只出現 13 張）", () => {
    expect(weakNeedsMore(true, 213, 227, true, false)).toBe(true);
    expect(weakNeedsMore(true, 213, 227, true, true)).toBe(false); // one page at a time
    expect(weakNeedsMore(true, 227, 227, false, false)).toBe(false);
    expect(weakNeedsMore(false, 100, 227, true, false)).toBe(false); // not asked: 「載入更多」 as usual
  });
});

describe("a scene the activity implies (SRCH-CNC-013)", () => {
  const t = i18n.getFixedT("zh-TW");
  it("[SRCH-CNC-013] 分隔線說明是因為「衝浪」而保留", () => {
    const v = softView(t, [{ word: "海邊", key: "scene.beach", shown: 2, implied_by: "衝浪" }], 5, []);
    expect(v?.divider).toBe("以下 3 張沒看出「海邊」，但都在「衝浪」");
    expect(v?.only).toBe("只看海邊（2 張）");
  });
});

