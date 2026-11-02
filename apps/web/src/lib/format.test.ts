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
