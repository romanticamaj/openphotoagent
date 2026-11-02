// Viewer performance budgets (09a VIEW-002/003/010, PERF-001/002/006) on a 100,000-photo library.
// Run with `just perf` (own Playwright project; not part of the PR e2e run). Thresholds come from 09a unchanged;
// PERF-001 is specified for the reference-spec runner — results from other machines are recorded, not excused.
import type { Page } from "@playwright/test";
import { expect, test } from "../fixtures";

test.use({ scenario: { photos: "library", library: true } });
test.describe.configure({ timeout: 600_000 });

const N = 100_000;

async function library100k(page: Page) {
  const r = await page.request.get("/api/v1/timeline/buckets");
  const month = (await r.json()).buckets[0].month;
  const real = (await (await page.request.get(`/api/v1/timeline/buckets/${month}`)).json()).assets[0];
  expect(real.thumbhash).toBeTruthy();
  await page.request.post("/__e2e/bulk-library", { data: { n: N, tag: "beach", thumbhash: real.thumbhash } });
  await page.goto("/");
  await expect(page.getByTestId("photo-grid")).toHaveAttribute("data-settled", "true", { timeout: 60_000 });
}

/** Wheel-scrolls the grid for `ms`, `px` per step. */
async function scroll(page: Page, ms: number, px: number, each?: (i: number) => Promise<void>) {
  const box = (await page.getByTestId("photo-grid").boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  const t0 = Date.now();
  let i = 0;
  while (Date.now() - t0 < ms) {
    await page.mouse.wheel(0, px);
    if (each) await each(i);
    i++;
    await page.waitForTimeout(16);
  }
  return i;
}

test("[VIEW-002] 排版在 Web Worker：捲動 10 萬張，主執行緒沒有 > 50 ms 的 long task", async ({ page }) => {
  await library100k(page);
  await page.evaluate(() => {
    const w = window as unknown as { __long: number[] };
    w.__long = [];
    new PerformanceObserver((l) => l.getEntries().forEach((e) => w.__long.push(Math.round(e.duration)))).observe({ type: "longtask" });
  });
  await scroll(page, 20_000, 1200);
  const long = await page.evaluate(() => (window as unknown as { __long: number[] }).__long.filter((d) => d > 50));
  expect(long, `long tasks: ${long.join(", ")} ms`).toEqual([]);
});

test("[VIEW-003] 無空白格：快速捲動 50 幀，沒有任何 tile 只有背景色", async ({ page }) => {
  await library100k(page);
  const blank: string[] = [];
  await scroll(page, 60_000, 2400, async (i) => {
    if (i >= 50) return;
    const shot = (await page.screenshot()).toString("base64");
    const bad = await page.evaluate(async (png) => {
      const img = new Image();
      img.src = `data:image/png;base64,${png}`;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.width;
      c.height = img.height;
      const g = c.getContext("2d")!;
      g.drawImage(img, 0, 0);
      const k = img.width / window.innerWidth;
      const out: string[] = [];
      // only the part of a tile visible inside the grid's scroll viewport (not what is clipped under the top bar)
      const v = document.querySelector('[data-testid="photo-grid"]')!.getBoundingClientRect();
      for (const el of Array.from(document.querySelectorAll<HTMLElement>('[role="gridcell"]'))) {
        const r = el.getBoundingClientRect();
        const x0 = Math.max(v.left, r.left + 4), y0 = Math.max(v.top, r.top + 4);
        const x1 = Math.min(v.right, r.right - 4), y1 = Math.min(v.bottom, r.bottom - 4);
        if (x1 - x0 < 16 || y1 - y0 < 16) continue; // only tiles substantially on screen
        const d = g.getImageData(x0 * k, y0 * k, (x1 - x0) * k, (y1 - y0) * k).data;
        let min = 255, max = 0;
        for (let p = 0; p < d.length; p += 4 * 7) {
          const l = 0.299 * d[p] + 0.587 * d[p + 1] + 0.114 * d[p + 2];
          if (l < min) min = l;
          if (l > max) max = l;
        }
        if (max - min < 3) out.push(el.getAttribute("aria-label") ?? "tile"); // a flat tile: background only
      }
      return out;
    }, shot);
    blank.push(...bad.map((b) => `frame ${i}: ${b}`));
  });
  expect(blank, blank.slice(0, 20).join("\n")).toEqual([]);
});

test("[VIEW-010] 記憶體預算：10 萬張捲動 3 分鐘 + 開 Lightbox 20 次，已解碼影像 ≤ 512 MB", async ({ page }) => {
  await library100k(page);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Performance.enable");
  await scroll(page, 180_000, 1500);
  for (let i = 0; i < 20; i++) {
    await page.getByRole("gridcell").nth(i % 5).click();
    await expect(page.getByTestId("lightbox")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("lightbox")).toHaveCount(0);
  }
  const m = Object.fromEntries((await cdp.send("Performance.getMetrics")).metrics.map((x) => [x.name, x.value]));
  const decoded = await page.evaluate(() =>
    Array.from(document.images).filter((im) => im.complete && im.naturalWidth > 0).reduce((s, im) => s + im.naturalWidth * im.naturalHeight * 4, 0),
  );
  const totalMb = (m.JSHeapUsedSize + decoded) / 2 ** 20;
  test.info().annotations.push({ type: "memory", description: `heap ${(m.JSHeapUsedSize / 2 ** 20).toFixed(0)} MB + decoded ${(decoded / 2 ** 20).toFixed(0)} MB` });
  expect(totalMb).toBeLessThanOrEqual(512);
});

test("[PERF-001] 10 萬張 60 fps：捲動 30 s，p95 frame ≤ 16.7 ms、掉幀 < 1%", async ({ page }) => {
  await library100k(page);
  await page.evaluate(() => {
    const w = window as unknown as { __frames: number[]; __stop: boolean };
    w.__frames = [];
    w.__stop = false;
    let last = performance.now();
    const tick = (t: number) => {
      w.__frames.push(t - last);
      last = t;
      if (!w.__stop) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await scroll(page, 30_000, 800);
  const frames = await page.evaluate(() => {
    const w = window as unknown as { __frames: number[]; __stop: boolean };
    w.__stop = true;
    return w.__frames.slice(5);
  });
  const sorted = [...frames].sort((a, b) => a - b);
  const p95 = sorted[Math.floor(sorted.length * 0.95)];
  const dropped = frames.filter((f) => f > 16.7 * 1.5).length / frames.length;
  test.info().annotations.push({ type: "frames", description: `n=${frames.length} p95=${p95.toFixed(1)} ms dropped=${(dropped * 100).toFixed(2)}%` });
  expect(p95).toBeLessThanOrEqual(16.7);
  expect(dropped).toBeLessThan(0.01);
});

test("[PERF-002] 首屏：熱快取 < 1 s", async ({ page }) => {
  await library100k(page);
  await page.reload();
  await expect(page.getByTestId("photo-grid")).toHaveAttribute("data-settled", "true");
  const t0 = Date.now();
  await page.reload();
  await expect(page.getByRole("gridcell").first()).toBeVisible();
  const ms = Date.now() - t0;
  test.info().annotations.push({ type: "first-screen", description: `${ms} ms` });
  expect(ms).toBeLessThan(1000);
});

test("[PERF-006] 搜尋延遲：10 萬張，文字 + facet，p95 < 300 ms", async ({ page }) => {
  await library100k(page);
  const times: number[] = [];
  for (let i = 0; i < 40; i++) {
    const t0 = Date.now();
    const r = await page.request.post("/api/v1/search", { data: { q: i % 2 ? "beach" : "photo beach", facets: ["category", "year"], limit: 60 } });
    expect(r.ok()).toBe(true);
    times.push(Date.now() - t0);
  }
  times.sort((a, b) => a - b);
  const p95 = times[Math.floor(times.length * 0.95)];
  test.info().annotations.push({ type: "search", description: `p50=${times[20]} ms p95=${p95} ms` });
  expect(p95).toBeLessThan(300);
});
