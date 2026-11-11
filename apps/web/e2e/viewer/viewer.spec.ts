import type { Page, Request } from "@playwright/test";
import { expect, test } from "../fixtures";

// a scanned library: 20 dated photos (one per month, 2023-01 … 2024-12) + 10 without EXIF (dated by file time)
test.use({ scenario: { photos: "library", library: true } });

const grid = (page: Page) => page.getByRole("grid", { name: "照片網格" });
const firstTile = (page: Page) => grid(page).getByRole("gridcell").first();
/** open a photo and wait for the dialog (the view transition commits the update asynchronously) */
async function open(page: Page, tile = firstTile(page)) {
  await tile.click();
  await expect(page.getByRole("dialog", { name: "照片檢視" })).toBeVisible();
}

test("[VIEW-005] scrubber：拖到 2023-03 < 100 ms 定位；role=slider 與 aria-valuetext", async ({ page }) => {
  await page.goto("/");
  const slider = page.getByRole("slider", { name: "時間軸" });
  await expect(slider).toHaveAttribute("aria-valuetext", /^\d{4} 年 \d{1,2} 月，\d+ 張$/);
  const box = (await slider.boundingBox())!;
  const buckets = (await (await page.request.get("/api/v1/timeline/buckets")).json()).buckets as Array<{ month: string; count: number }>;
  const total = buckets.reduce((n, b) => n + b.count, 0);
  const i = buckets.findIndex((b) => b.month === "2023-03");
  const before = buckets.slice(0, i).reduce((n, b) => n + b.count, 0);
  const y = box.y + box.height * ((before + 0.5) / total);
  await page.mouse.move(box.x + box.width / 2, box.y + 2);
  await page.mouse.down();
  const t0 = Date.now();
  await page.mouse.move(box.x + box.width / 2, y);
  await expect(slider).toHaveAttribute("aria-valuetext", "2023 年 3 月，1 張", { timeout: 1000 });
  const section = page.locator('section[data-month="2023-03"]');
  await expect(section).toBeInViewport();
  const elapsed = Date.now() - t0;
  await page.mouse.up();
  expect(elapsed).toBeLessThan(100 + 150); // budget + Playwright round-trip allowance (measured in-page below)
  const inPage = await page.evaluate(async () => {
    const s = document.querySelector<HTMLElement>('[role="slider"]')!;
    const r = s.getBoundingClientRect();
    const t = performance.now();
    s.dispatchEvent(new PointerEvent("pointerdown", { clientX: r.x + 10, clientY: r.y + r.height * 0.97, bubbles: true, pointerId: 1 }));
    await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
    return performance.now() - t;
  });
  expect(inPage).toBeLessThan(100);
});

test("[VIEW-006] 時間軸鍵盤：PgUp／PgDn／Home／End／G", async ({ page }) => {
  await page.goto("/");
  await firstTile(page).focus();
  const scroller = page.getByTestId("photo-grid");
  const top = () => scroller.evaluate((el) => el.scrollTop);
  const screen = await scroller.evaluate((el) => el.clientHeight);
  await page.keyboard.press("PageDown");
  await expect.poll(top).toBeGreaterThanOrEqual(screen - 2);
  await page.keyboard.press("PageUp");
  await expect.poll(top).toBe(0);
  await page.keyboard.press("End");
  await expect.poll(top).toBeGreaterThan(screen);
  await page.keyboard.press("Home");
  await expect.poll(top).toBe(0);
  await page.keyboard.press("g");
  const dialog = page.getByRole("dialog", { name: "跳到日期" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("textbox").fill("2023 年 3 月");
  await dialog.getByRole("textbox").press("Enter");
  await expect(dialog).toBeHidden();
  await expect(page.locator('section[data-month="2023-03"]')).toBeInViewport();
  await expect(page.getByRole("slider")).toHaveAttribute("aria-valuetext", "2023 年 3 月，1 張");
});

test("[A11Y-002] 網格語意：grid/row/gridcell、roving tabindex、aria-rowcount／rowindex", async ({ page }) => {
  await page.goto("/");
  const g = grid(page);
  await expect(g).toHaveAttribute("aria-rowcount", /^\d+$/);
  const rows = g.getByRole("row");
  await expect(rows.first()).toHaveAttribute("aria-rowindex", "1");
  const cells = g.getByRole("gridcell");
  await expect(cells.first()).toBeVisible();
  expect(await g.locator('[role="gridcell"][tabindex="0"]').count()).toBe(1);
  await cells.first().focus();
  await page.keyboard.press("ArrowRight");
  await expect(cells.nth(1)).toBeFocused();
  expect(await g.locator('[role="gridcell"][tabindex="0"]').count()).toBe(1);
  await expect(cells.nth(1)).toHaveAttribute("tabindex", "0");
  await page.keyboard.press("ArrowLeft");
  await expect(cells.first()).toBeFocused();
});

test("[A11Y-003] Lightbox：role=dialog aria-modal；切換時 aria-live 播報「第 n 張，共 N 張」", async ({ page }) => {
  await page.goto("/");
  await open(page);
  const dlg = page.getByRole("dialog", { name: "照片檢視" });
  await expect(dlg).toHaveAttribute("aria-modal", "true");
  const pos = page.getByTestId("lightbox-position");
  await expect(pos).toHaveAttribute("aria-live", "polite");
  await expect(pos).toHaveText("第 1 張，共 30 張");
  await page.keyboard.press("ArrowRight");
  await expect(pos).toHaveText("第 2 張，共 30 張");
  await page.keyboard.press("Escape");
  await expect(dlg).toBeHidden();
  await expect(page.locator(":focus")).toHaveAttribute("role", "gridcell"); // focus returns to the grid
});

test("[VIEW-008] 漸進載入順序：ThumbHash → t512 → p1024", async ({ page }) => {
  await page.goto("/");
  await expect(firstTile(page)).toHaveCSS("background-image", /^url\("data:image\/png/); // ThumbHash painted first
  const seen: string[] = [];
  page.on("request", (r: Request) => {
    const m = /\/assets\/([^/]+)\/media\?variant=(\w+)/.exec(r.url());
    if (m) seen.push(`${m[1]}:${m[2]}`);
  });
  const id = await firstTile(page).getAttribute("data-asset");
  await firstTile(page).click();
  await expect(page.locator('[data-testid="lightbox-stage"] img[data-variant="p1024"]')).toHaveJSProperty("complete", true);
  const mine = seen.filter((s) => s.startsWith(`${id}:`)).map((s) => s.split(":")[1]).filter((v) => v !== "t256");
  expect(mine.slice(0, 2)).toEqual(["t512", "p1024"]);
  await expect(page.locator('[data-testid="lightbox-stage"]')).toHaveCSS("background-image", /data:image\/png/);
});

test("[VIEW-007] 轉場降級：沒有 startViewTransition 時改用 FLIP，功能不受影響", async ({ page }) => {
  await page.addInitScript(() => {
    // simulate WebKitGTK
    delete (Document.prototype as unknown as Record<string, unknown>).startViewTransition;
    const anims: string[] = [];
    (window as unknown as { __anims: string[] }).__anims = anims;
    const orig = Element.prototype.animate;
    Element.prototype.animate = function (k, o) {
      anims.push((this as HTMLElement).dataset.testid ?? this.tagName);
      return orig.call(this, k, o);
    };
  });
  await page.goto("/");
  expect(await page.evaluate(() => "startViewTransition" in document)).toBe(false);
  await firstTile(page).click();
  await expect(page.getByRole("dialog", { name: "照片檢視" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __anims: string[] }).__anims)).toContain("lightbox-stage");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("lightbox-position")).toHaveText("第 2 張，共 30 張");
});

test("[E2E-L-001] Lightbox 完整：縮放、平移、←/→、filmstrip、I 資訊面板", { tag: ["@critical"] }, async ({ page, request, stack }) => {
  // AI results for the info panel: organize the library by content first (FakeBackend)
  const job = await (
    await request.post("/api/v1/jobs", {
      data: { kind: "organize", preset: "by-content-12", source_id: stack.source_id, target: { root_alias: "sorted", sub_path: "" } },
    })
  ).json();
  await expect.poll(async () => (await (await request.get(`/api/v1/jobs/${job.id}`)).json()).status, { timeout: 60_000 }).toBe("awaiting_approval");

  const preloads: string[] = [];
  page.on("request", (r) => {
    const m = /variant=(t512)/.exec(r.url());
    if (m) preloads.push(r.url());
  });
  await page.goto("/");
  const dated = page.locator('section[data-month="2024-12"] [role="gridcell"]');
  const start = await (await page.request.get("/api/v1/timeline/buckets")).json();
  const n = start.buckets.slice(0, start.buckets.findIndex((b: { month: string }) => b.month === "2024-12")).reduce((a: number, b: { count: number }) => a + b.count, 0) + 1;
  const neighbour = await page.request.get("/api/v1/timeline/buckets/2024-11");
  const second = (await neighbour.json()).assets[0].id as string;
  await dated.scrollIntoViewIfNeeded();
  await open(page, dated);
  const stage = page.getByTestId("lightbox-stage");
  await expect(stage.locator('img[data-variant="p1024"]')).toBeVisible();
  // neighbours preloaded
  await expect.poll(() => preloads.some((u) => u.includes(second!))).toBe(true);
  // zoom and pan
  await page.getByRole("button", { name: "放大" }).click();
  await expect(stage).toHaveAttribute("data-scale", "1.5");
  const box = (await stage.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 + 20);
  await page.mouse.up();
  await expect(stage.locator('img[data-variant="p1024"]')).toHaveAttribute("style", /translate\(40px, 20px\) scale\(1\.5\)/);
  await page.keyboard.press("0");
  await expect(stage).toHaveAttribute("data-scale", "1");
  // ← / →
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("lightbox-position")).toHaveText(`第 ${n + 1} 張，共 30 張`);
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByTestId("lightbox-position")).toHaveText(`第 ${n} 張，共 30 張`);
  // filmstrip
  const film = page.getByRole("navigation", { name: "縮圖列" });
  await film.getByRole("button", { name: `第 ${n + 2} 張，共 30 張` }).click();
  await expect(page.getByTestId("lightbox-position")).toHaveText(`第 ${n + 2} 張，共 30 張`);
  await film.getByRole("button", { name: `第 ${n} 張，共 30 張` }).click();
  // I: info panel with EXIF, AI result, model name and cascade layer
  await page.keyboard.press("i");
  const info = page.getByTestId("info-panel");
  await expect(info.getByText("相機", { exact: true })).toBeVisible();
  await expect(info.getByText("OPA Synth-1")).toBeVisible();
  await expect(info.getByTestId("ai-label").first()).toBeVisible();
  await expect(info.getByTestId("ai-model").first()).not.toBeEmpty();
  await expect(info.getByTestId("ai-layer").first()).toHaveText(/影像相似度|視覺語言模型|兩者一致|規則/);
});

test("[E2E-L-002] 就地修正：Lightbox 改類別 → toast「已記錄修正」→ 下一次整理計畫使用修正值", async ({ page, request, stack }) => {
  await page.goto("/");
  const id = await firstTile(page).getAttribute("data-asset");
  await open(page);
  await page.keyboard.press("i");
  const info = page.getByTestId("info-panel");
  await info.getByLabel("修正類別").selectOption({ label: "動物" });
  await info.getByRole("button", { name: "儲存修正" }).click();
  await expect(page.getByRole("status").filter({ hasText: "已記錄修正" })).toBeVisible();
  await expect(info.getByTestId("human-label")).toContainText("動物");
  // the next organize plan uses the correction
  const job = await (
    await request.post("/api/v1/jobs", {
      data: { kind: "organize", preset: "by-content-12", source_id: stack.source_id, target: { root_alias: "sorted", sub_path: "" } },
    })
  ).json();
  let j = job;
  await expect
    .poll(async () => (j = await (await request.get(`/api/v1/jobs/${job.id}`)).json()).status, { timeout: 60_000 })
    .toBe("awaiting_approval");
  const plan = await (await request.get(`/api/v1/output-plans/${j.plan_id}`)).json();
  const act = plan.actions.find((a: { asset_id: string }) => a.asset_id === id);
  expect(act.dst_rel).toMatch(/^動物\//);
});

test("[E2E-L-003] 在檔案總管顯示：desktop build 呼叫 platform.revealInFileManager；web build 隱藏", async ({ page }) => {
  await page.goto("/");
  await open(page);
  await page.keyboard.press("i");
  await expect(page.getByTestId("info-panel").getByText(/^photos\//)).toBeVisible();
  await expect(page.getByRole("button", { name: "在檔案總管中顯示" })).toHaveCount(0);

  await page.addInitScript(() => {
    const calls: Array<[string, string]> = [];
    (window as unknown as { __reveal: typeof calls }).__reveal = calls;
    (window as unknown as { __OPA_PLATFORM__: unknown }).__OPA_PLATFORM__ = {
      kind: "desktop",
      revealInFileManager: async (a: string, r: string) => void calls.push([a, r]),
    };
  });
  await page.reload();
  await open(page);
  await page.keyboard.press("i");
  const rel = (await page.getByTestId("info-panel").getByText(/^photos\//).first().textContent())!.replace(/^photos\//, "");
  await page.getByRole("button", { name: "在檔案總管中顯示" }).first().click();
  expect(await page.evaluate(() => (window as unknown as { __reveal: unknown }).__reveal)).toEqual([["photos", rel]]);
});

test("[VIEW-043] 回歸：Ctrl+A 選取整個圖庫、不反白頁面文字；Shift+方向鍵延伸選取（驗證第 52 輪）", async ({ page }) => {
  await page.goto("/");
  await firstTile(page).focus();
  await page.keyboard.press("ControlOrMeta+a");
  const bar = page.getByTestId("timeline-batch");
  await expect(bar).toContainText("已選 30 張"); // 20 dated + 10 without EXIF: the whole library, not the months on screen
  expect(await page.evaluate(() => window.getSelection()?.toString() ?? "")).toBe("");
  await grid(page).getByRole("gridcell").nth(1).click(); // taken back out
  await expect(bar).toContainText("已選 29 張");
  await page.keyboard.press("Escape");
  await expect(bar).toHaveCount(0);
  await firstTile(page).click({ modifiers: ["ControlOrMeta"] });
  await page.keyboard.press("Shift+ArrowRight");
  await page.keyboard.press("Shift+ArrowRight");
  await expect(bar).toContainText("已選 3 張");
  await expect(grid(page).getByRole("gridcell").nth(2)).toBeFocused();
});

test("[VIEW-044] 回歸：在照片頁任何地方按 Ctrl+A 都是全選；Shift+← 縮回選取（驗證第 53 輪）", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "照片", exact: true }).first().click(); // focus on the nav, not on the grid
  await page.keyboard.press("ControlOrMeta+a");
  const bar = page.getByTestId("timeline-batch");
  await expect(bar).toContainText("已選 30 張");
  expect(await page.evaluate(() => window.getSelection()?.toString() ?? "")).toBe("");
  await page.keyboard.press("Escape");
  await firstTile(page).click({ modifiers: ["ControlOrMeta"] });
  await page.keyboard.press("Shift+ArrowRight");
  await page.keyboard.press("Shift+ArrowRight");
  await expect(bar).toContainText("已選 3 張");
  await page.keyboard.press("Shift+ArrowLeft");
  await expect(bar).toContainText("已選 2 張");
  await expect(grid(page).getByRole("gridcell").nth(1)).toBeFocused();
  await expect(grid(page).getByRole("gridcell").nth(2)).toHaveAttribute("aria-selected", "false");
});

test("[VIEW-046] 回歸：Shift 範圍越過起點往回時，另一邊會放掉；燈箱裡 Ctrl+A 不反白頁面（驗證第 54 輪）", async ({ page }) => {
  await page.goto("/");
  const cells = grid(page).getByRole("gridcell");
  await cells.nth(3).click({ modifiers: ["ControlOrMeta"] });
  await page.keyboard.press("Shift+ArrowRight"); // 3, 4
  await page.keyboard.press("Shift+ArrowLeft"); // 3
  await page.keyboard.press("Shift+ArrowLeft"); // 2, 3
  const bar = page.getByTestId("timeline-batch");
  await expect(bar).toContainText("已選 2 張");
  await expect(cells.nth(4)).toHaveAttribute("aria-selected", "false");
  await expect(cells.nth(2)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await open(page);
  await page.keyboard.press("ControlOrMeta+a");
  expect(await page.evaluate(() => window.getSelection()?.toString() ?? "")).toBe("");
});

test("[A11Y-013] 回歸：第一次按 Tab 出現「跳到主要內容」，按下去焦點到第一張照片（驗證第 54～55 輪：要按 12～15 次 Tab）", async ({ page }) => {
  await page.goto("/");
  await expect(firstTile(page)).toBeVisible();
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "跳到主要內容" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await page.keyboard.press("Enter");
  await expect(firstTile(page)).toBeFocused();
});
