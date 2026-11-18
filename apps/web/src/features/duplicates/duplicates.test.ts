import { describe, expect, it } from "vitest";
import { cardMembers } from "./cardMembers";

describe("a duplicate group's card (audit round 53)", () => {
  it("[SRCH-DUP-009] 回歸：代表張在第一張並標示，最多顯示 4 張，其餘寫「+N」（驗證第 53 輪：10 張擠出卡片、頁面橫向捲動）", () => {
    const members = Array.from({ length: 10 }, (_, i) => ({ asset_id: `a${i}` }));
    const c = cardMembers(members, "a7", 4);
    expect(c.shown.map((m) => m.asset_id)).toEqual(["a7", "a0", "a1", "a2"]);
    expect(c.more).toBe(6);
    const two = cardMembers(members.slice(0, 2), "a0", 4);
    expect(two.shown.map((m) => m.asset_id)).toEqual(["a0", "a1"]);
    expect(two.more).toBe(0);
  });
});
