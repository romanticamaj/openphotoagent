import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("the API client", () => {
  it("[API-UI-001] 回歸：沒有內容的成功回應（204）不當成錯誤（驗證第 140 輪：回憶的「復原」每次都失敗，Unexpected end of JSON input）", async () => {
    const fetch = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await expect(api.memoryFeedbackRemove("f1")).resolves.toBeUndefined();
    expect(String((fetch.mock.calls[0] as unknown as [string, RequestInit])[0])).toContain("/api/v1/memories/feedback/f1");
  });
});
