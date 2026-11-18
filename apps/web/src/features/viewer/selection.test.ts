import { describe, expect, it } from "vitest";
import { NONE, pick } from "./selection";

const order = ["a", "b", "c", "d", "e"];

describe("choosing photos on the timeline (audit round 50)", () => {
  it("[VIEW-037] Ctrl 點選切換一張；Shift 點選從上一張選到這一張（可往回）；再按一次取消", () => {
    let s = pick(NONE, "b", "toggle", order);
    expect([...s.ids]).toEqual(["b"]);
    s = pick(s, "d", "range", order);
    expect([...s.ids].sort()).toEqual(["b", "c", "d"]);
    s = pick(s, "a", "range", order); // from the last one picked, backwards
    expect([...s.ids].sort()).toEqual(["a", "b", "c", "d"]);
    s = pick(s, "c", "toggle", order);
    expect([...s.ids].sort()).toEqual(["a", "b", "d"]);
    expect(s.anchor).toBe("c");
    // Shift with nothing picked yet, or an anchor no longer loaded: just that photo
    expect([...pick(NONE, "e", "range", order).ids]).toEqual(["e"]);
    expect([...pick({ ids: new Set(["x"]), anchor: "x" }, "e", "range", order).ids].sort()).toEqual(["e", "x"]);
  });
});

describe("Ctrl+A on the timeline (audit round 52)", () => {
  it("[VIEW-042] 回歸：Ctrl+A 選取整個圖庫；之後點一張是取消那一張；張數是全部減去取消的", async () => {
    const { all, chosen, countOf } = await import("./selection");
    let s = all();
    expect(chosen(s, "a")).toBe(true);
    expect(countOf(s, 30)).toBe(30);
    s = pick(s, "b", "toggle", order);
    expect(chosen(s, "b")).toBe(false);
    expect(countOf(s, 30)).toBe(29);
    s = pick(s, "b", "toggle", order);
    expect(chosen(s, "b")).toBe(true);
    expect(countOf(NONE, 30)).toBe(0);
    expect(countOf(pick(NONE, "a", "toggle", order), 30)).toBe(1);
  });
});

describe("a Shift range runs from its anchor (audit round 54)", () => {
  it("[VIEW-045] 回歸：Shift 範圍永遠是錨點到目前這張，再加上開始前已選的；越過錨點往回時另一邊放掉（驗證第 54 輪：[4,5,6] 而不是 [4,5]）", async () => {
    const { span } = await import("./selection");
    const order7 = ["p1", "p2", "p3", "p4", "p5", "p6", "p7"];
    const base = pick(NONE, "p1", "toggle", order7); // chosen before the range
    const ids = (s: { ids: Set<string> }) => [...s.ids].sort();
    expect(ids(span(base, "p5", "p7", order7))).toEqual(["p1", "p5", "p6", "p7"]);
    expect(ids(span(base, "p5", "p6", order7))).toEqual(["p1", "p5", "p6"]);
    expect(ids(span(base, "p5", "p4", order7))).toEqual(["p1", "p4", "p5"]);
    expect(span(base, "p5", "p4", order7).anchor).toBe("p5"); // the anchor stays
  });
});
