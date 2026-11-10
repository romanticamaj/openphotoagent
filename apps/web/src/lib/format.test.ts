import { etaText } from "./format";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { formatBytes, formatDuration, photoCount } from "./format";

describe("formatting", () => {
  it("[I18N-003] counts and plurals", () => {
    expect(photoCount(1, "en")).toBe("1 photo");
    expect(photoCount(2, "en")).toBe("2 photos");
    expect(photoCount(1284, "en")).toBe("1,284 photos");
    expect(photoCount(1284, "zh-TW")).toBe("1,284 張");
    expect(photoCount(0, "zh-TW")).toBe("0 張");
  });

  const real = (Intl as unknown as { DurationFormat?: unknown }).DurationFormat;
  afterEach(() => {
    (Intl as unknown as { DurationFormat?: unknown }).DurationFormat = real;
  });

  it("[I18N-006] without Intl.DurationFormat the fallback matches the golden", () => {
    delete (Intl as unknown as { DurationFormat?: unknown }).DurationFormat; // WebKitGTK
    const golden = JSON.parse(readFileSync(join(import.meta.dirname, "../../../../fixtures/golden/duration.json"), "utf8"));
    for (const c of golden.cases) {
      expect(formatDuration(c.ms, c.locale)).toBe(c.text);
    }
  });

  it("byte sizes use decimal units with one significant decimal", () => {
    expect(formatBytes(999, "en")).toBe("999 B");
    expect(formatBytes(1_500_000, "en")).toBe("1.5 MB");
    expect(formatBytes(5_234_000_000, "zh-TW")).toBe("5.2 GB");
    expect(formatBytes(12_000_000_000, "en")).toBe("12 GB");
  });
});

describe("[ETA-025] the remaining time is readable", () => {
  it("[ETA-025] rough, precise and stalled show words, not only a state", () => {
    expect(etaText({ state: "rough", p50_s: 30 }, "zh-TW")).toBe("不到 1 分鐘");
    expect(etaText({ state: "rough", p50_s: 180 }, "zh-TW")).toBe("約 3 分鐘");
    expect(etaText({ state: "rough", p50_s: 4800 }, "zh-TW")).toBe("約 1 小時 20 分鐘");
    expect(etaText({ state: "rough", p50_s: 180 }, "en")).toBe("About 3 min");
    expect(etaText({ state: "precise", p50_s: 161 }, "zh-TW")).toBe("剩 2:41");
    expect(etaText({ state: "stalled", since_s: 80, p50_s: 10 }, "en")).toContain("No progress for");
  });

  it("[ETA-032] 回歸：剩不到一分鐘、樣本夠時說約幾秒（驗證第 21 輪：「不到 1 分鐘」停了一分鐘）", () => {
    expect(etaText({ state: "rough", p50_s: 38, samples: 5 }, "zh-TW")).toBe("約 40 秒");
    expect(etaText({ state: "rough", p50_s: 4, samples: 5 }, "zh-TW")).toBe("約 10 秒");
    expect(etaText({ state: "rough", p50_s: 38, samples: 5 }, "en")).toBe("About 40 s");
    expect(etaText({ state: "rough", p50_s: 38, samples: 1 }, "zh-TW")).toBe("不到 1 分鐘"); // too few samples to say more
  });
  it("[ETA-025] nothing to say while estimating", () => {
    expect(etaText({ state: "estimating" }, "zh-TW")).toBeNull();
    expect(etaText({ state: "unknown" }, "en")).toBeNull();
  });
});

