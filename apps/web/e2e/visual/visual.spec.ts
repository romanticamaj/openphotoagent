import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "../fixtures";
import { ROUTES } from "../routes";

// component stories come from the built Storybook (ADR-0003); fonts are bundled so baselines are reproducible
const SB = "http://127.0.0.1:6007";
type Entry = { id: string; title: string; name: string; type: string };

async function stories(page: Page, prefix: string): Promise<Entry[]> {
  const idx = (await (await page.request.get(`${SB}/index.json`)).json()) as { entries: Record<string, Entry> };
  return Object.values(idx.entries).filter((e) => e.type === "story" && e.title.startsWith(prefix));
}

/** Fonts loaded, no request in flight (the event stream aside), every image decoded and the layout unchanged for
 * three consecutive checks — snapshots must not depend on how fast thumbnails and layout arrive. */
const inflight = new WeakMap<Page, Set<string>>();
function track(page: Page) {
  if (inflight.has(page)) return;
  const set = new Set<string>();
  inflight.set(page, set);
  page.on("request", (r) => !r.url().includes("/events") && set.add(r.url() + r.method()));
  const done = (r: { url(): string; method(): string }) => set.delete(r.url() + r.method());
  page.on("requestfinished", done);
  page.on("requestfailed", done);
}

async function settle(page: Page) {
  track(page);
  await page.evaluate(() => document.fonts.ready);
  const grid = page.getByTestId("photo-grid");
  if (await grid.count()) await expect(grid).toHaveAttribute("data-settled", "true", { timeout: 15_000 });
  let last = "";
  let same = 0;
  for (let i = 0; i < 80 && same < 3; i++) {
    await page.waitForTimeout(150);
    const state = await page.evaluate(() => {
      const pending = Array.from(document.images).filter((im) => !im.complete).length;
      const boxes = Array.from(document.querySelectorAll("[role=gridcell],[role=row],img,section"))
        .slice(0, 300)
        .map((e) => e.getBoundingClientRect())
        .map((r) => `${Math.round(r.x)},${Math.round(r.y)},${Math.round(r.width)},${Math.round(r.height)}`)
        .join(";");
      return `${pending}|${boxes}`;
    });
    const quiet = state.startsWith("0|") && (inflight.get(page)?.size ?? 0) === 0;
    same = quiet && state === last ? same + 1 : 0;
    last = state;
  }
}

async function shot(page: Page, id: string, theme: string) {
  await page.goto(`${SB}/iframe.html?id=${id}&globals=theme:${theme}&viewMode=story`);
  await page.locator("#storybook-root > *").first().waitFor();
  await page.evaluate(() => document.fonts.ready);
}

const COMPONENTS = ["Activity/OperationProgress", "Organize/ReadinessChecklist", "Components/CompatBadge", "Components/GatedButton", "Errors/ProblemCard"];

for (const theme of ["light", "dark"]) {
  test(`[VIS-001] 元件視覺回歸 — ${theme}`, async ({ page }) => {
    test.setTimeout(120_000);
    for (const prefix of COMPONENTS.slice(0, 4)) {
      for (const s of await stories(page, prefix)) {
        await shot(page, s.id, theme);
        await expect(page.locator("#storybook-root")).toHaveScreenshot(`${s.id}-${theme}.png`);
      }
    }
    // ProblemCard: one representative per density in VIS-001; every code is covered by VIS-003
    await shot(page, "errors-problemcard--net-unreachable", theme);
    await expect(page.locator("#storybook-root")).toHaveScreenshot(`problemcard-net-unreachable-${theme}.png`);
  });

  test(`[ACT-006] OperationProgress 四變體 × eta 狀態 — ${theme}`, async ({ page }) => {
    for (const s of await stories(page, "Activity/OperationProgress")) {
      await shot(page, s.id, theme);
      await expect(page.locator("#storybook-root")).toHaveScreenshot(`act006-${s.id}-${theme}.png`);
    }
    await shot(page, "activity-operationprogress--full", theme);
    const skipped = page.locator('.op-step[data-status="skipped"]').first();
    await expect(skipped).toHaveCSS("border-top-style", "dashed");
    const muted = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--text-muted").trim());
    expect(muted.length).toBeGreaterThan(0);
  });
}

test("[VIS-003] 每個錯誤碼一個 story：數量 = 錯誤碼目錄，且每個都有快照", async ({ page }) => {
  test.setTimeout(300_000);
  const catalog = join(import.meta.dirname, "..", "..", "..", "..", "packages", "i18n", "errors.zh-TW.json");
  const codes = Object.keys(JSON.parse(readFileSync(catalog, "utf8")));
  const list = await stories(page, "Errors/ProblemCard");
  expect(list.length).toBe(codes.length);
  for (const s of list) {
    await shot(page, s.id, "light");
    await expect(page.locator("#storybook-root")).toHaveScreenshot(`vis003-${s.id}.png`);
  }
});

test("[PF-080] 停用狀態可近用：每個 disabled story 都有 aria-describedby 指向原因；鍵盤 focus 可到達", async ({ page }) => {
  const gated = (await stories(page, "Components/GatedButton")).filter((s) => !s.id.endsWith("--enabled"));
  expect(gated.length).toBeGreaterThanOrEqual(4);
  for (const s of gated) {
    await shot(page, s.id, "light");
    const btn = page.locator("#storybook-root button");
    await expect(btn).toHaveAttribute("aria-disabled", "true");
    const why = await btn.getAttribute("aria-describedby");
    expect(why).toBeTruthy();
    await expect(page.locator(`[id="${why}"]`)).toBeVisible();
    await expect(btn).toHaveAccessibleDescription(/\S/);
    for (let i = 0; i < 5 && !(await btn.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press("Tab");
    await expect(btn).toBeFocused(); // reachable by keyboard although gated
  }
});

test.describe("pages", () => {
  // dated photos only: page snapshots must not depend on when the repository was checked out
  test.use({ scenario: { photos: "trip", library: true, organize: "by-content-12" } });
  for (const theme of ["light", "dark"] as const) {
    for (const lang of ["zh-TW", "en"]) {
      test(`[VIS-002] 頁面視覺回歸 — ${theme} · ${lang}`, async ({ page, stack }) => {
        test.setTimeout(180_000);
        track(page);
        await page.emulateMedia({ colorScheme: theme });
        for (const r of ROUTES) {
          await r.open(page, stack, lang);
          await settle(page);
          await expect(page).toHaveScreenshot(`page-${r.name.replace(/\s/g, "-")}-${theme}-${lang}.png`, {
            fullPage: false,
            // time-dependent text: elapsed time, ETA, timestamps, IDs
            mask: [page.locator('[data-testid="elapsed"]'), page.locator("[data-eta]"), page.locator("time"), page.locator("[translate=no]")],
          });
        }
      });
    }
  }
});
