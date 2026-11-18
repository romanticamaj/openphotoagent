import { describe, expect, it } from "vitest";
import { isDegraded } from "./api";

describe("search notice", () => {
  it("[SRCH-SEM-007] 回歸：degraded 是清單，空清單不算降級（空陣列在 JS 是真值，提示永遠出現）", () => {
    expect(isDegraded({ degraded: [] })).toBe(false);
    expect(isDegraded({ degraded: ["image_vector"] })).toBe(true);
    expect(isDegraded(undefined)).toBe(false);
  });
});
