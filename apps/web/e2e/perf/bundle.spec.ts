// PERF-003: the photos route's first load ≤ 180 KB gzip; all JS ≤ 250 KB gzip (09a; size-limit equivalent).
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { expect, test } from "@playwright/test";

const dist = join(dirname(fileURLToPath(import.meta.url)), "../../dist");
const gz = (f: string) => gzipSync(readFileSync(join(dist, f)), { level: 9 }).length;

test("[PERF-003] bundle 大小：照片路由首次 ≤ 180 KB gzip；總預算 250 KB", async ({ page }) => {
  const loaded = new Set<string>();
  page.on("response", (r) => {
    const m = new URL(r.url()).pathname.match(/^\/(assets\/.+\.(?:js|css))$/);
    if (m) loaded.add(m[1]);
  });
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  const first = [...loaded].reduce((s, f) => s + gz(f), 0);
  const all = readdirSync(join(dist, "assets")).filter((f) => f.endsWith(".js")).reduce((s, f) => s + gz(join("assets", f)), 0);
  test.info().annotations.push({ type: "bundle", description: `first ${(first / 1024).toFixed(1)} KB, all JS ${(all / 1024).toFixed(1)} KB` });
  expect(first / 1024).toBeLessThanOrEqual(180);
  expect(all / 1024).toBeLessThanOrEqual(250);
});
