// PERF-003 (ADR-0006): first-load JavaScript, gzip — the photos route ≤ 180 KB, every route ≤ 250 KB
// (10 §5.1 "JS bundle", 04 "首次載入 JS ≤ 250 KB"). CSS (app + font-face declarations) is recorded, not budgeted.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import type { Page } from "@playwright/test";
import { expect, test } from "../fixtures";

test.use({ scenario: { photos: "library", library: true, organize: "by-content-12" } });

const dist = join(dirname(fileURLToPath(import.meta.url)), "../../dist");
const gz = (f: string) => gzipSync(readFileSync(join(dist, f)), { level: 9 }).length;
const ROUTES = ["/", "/organize", "/review", "/models", "/system/doctor", "/settings", "/search?q=photo", "/welcome"];

async function firstLoad(page: Page, path: string) {
  const files = new Set<string>();
  const on = (r: { url(): string }) => {
    const m = new URL(r.url()).pathname.match(/^\/(assets\/.+\.(?:js|css))$/);
    if (m) files.add(m[1]);
  };
  await page.context().clearCookies();
  const fresh = await page.context().browser()!.newContext();
  const p = await fresh.newPage();
  p.on("response", on);
  await p.goto(new URL(path, page.url() === "about:blank" ? test.info().project.use.baseURL! : page.url()).toString());
  await p.waitForLoadState("networkidle");
  await fresh.close();
  const js = [...files].filter((f) => f.endsWith(".js")).reduce((s, f) => s + gz(f), 0);
  const css = [...files].filter((f) => f.endsWith(".css")).reduce((s, f) => s + gz(f), 0);
  return { js: js / 1024, css: css / 1024 };
}

test("[PERF-003] bundle 大小：照片路由首次 JS ≤ 180 KB gzip；每路由首次 JS ≤ 250 KB", async ({ page }) => {
  const rows: string[] = [];
  let photos = 0;
  let worst = 0;
  for (const r of ROUTES) {
    const { js, css } = await firstLoad(page, r);
    rows.push(`${r}: JS ${js.toFixed(1)} KB, CSS ${css.toFixed(1)} KB`);
    if (r === "/") photos = js;
    worst = Math.max(worst, js);
  }
  test.info().annotations.push({ type: "bundle", description: rows.join("; ") });
  expect(photos).toBeLessThanOrEqual(180);
  expect(worst, rows.join("\n")).toBeLessThanOrEqual(250);
});
