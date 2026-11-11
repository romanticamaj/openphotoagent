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
