import { expect, test } from "../fixtures";

// v0.4 viewer additions (10 §2.5, §3.9): an offline map with clusters and borders, and a 2–4 photo compare mode.
test.describe("map", () => {
  test.use({ scenario: { photos: "trip", library: true } });

  test("[MAP-001] 離線地圖：國界與照片聚類，不請求任何外部圖磚", async ({ page, request }) => {
    const lib = await (await request.post("/__e2e/gps-library")).json();
    expect(lib.with_gps).toBe(20);
    const external: string[] = [];
    page.on("request", (r) => {
      const u = new URL(r.url());
      if (u.protocol === "blob:" || u.protocol === "data:") return; // MapLibre's own worker and inline data
      if (!["127.0.0.1", "localhost"].includes(u.hostname)) external.push(r.url());
    });
    await page.goto("/map");
    const map = page.getByRole("region", { name: "地圖" });
    await expect(map.getByTestId("map-borders")).toHaveAttribute("data-countries", /\d{3}/); // Natural Earth 110m: 170+ countries
    const clusters = map.getByRole("button", { name: /張照片/ });
    await expect(clusters.first()).toBeVisible();
    const total = (await clusters.evaluateAll((els) => els.map((e) => Number(e.getAttribute("data-count"))))).reduce((a, b) => a + b, 0);
    expect(total).toBe(20);
    expect(external).toEqual([]);
  });

  test("[MAP-011] 回歸：地圖填滿區域，聚類真的看得到（安裝版是一個空的暗框）", async ({ page, request }) => {
    // maplibre-gl.css sets .maplibregl-map { position: relative } after Tailwind, so the container lost its size and
    // everything was clipped; Playwright's toBeVisible ignores clipping by an ancestor, hence this check.
    await request.post("/__e2e/gps-library");
    await page.goto("/map");
    const region = page.getByRole("region", { name: "地圖" }).first(); // the page section; MapLibre adds another
    await expect(region.getByRole("button", { name: /張照片/ }).first()).toBeVisible();
    const r = await region.locator(".maplibregl-map").locator("..").boundingBox(); // the box the map should fill
    await expect.poll(async () => (await region.locator(".maplibregl-canvas").boundingBox())!.height).toBeGreaterThan(r!.height * 0.9);
    const onTop = await region.getByRole("button", { name: /張照片/ }).first().evaluate((el) => {
      const b = el.getBoundingClientRect();
      return document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2) === el;
    });
    expect(onTop).toBe(true);
  });

  test("[MAP-002] 點聚類 → 網格只剩該範圍的照片；沒有位置的照片另外計數", async ({ page, request }) => {
    await request.post("/__e2e/gps-library");
    await page.goto("/map");
    const map = page.getByRole("region", { name: "地圖" });
    await expect(page.getByTestId("map-no-location")).toContainText(/沒有位置：\s*\d+/);
    const c = map.getByRole("button", { name: /張照片/ }).first();
    const n = Number(await c.getAttribute("data-count"));
    await c.dispatchEvent("click"); // markers move with every map frame; the click itself is what matters
    await expect(page.getByTestId("map-selection").getByRole("img")).toHaveCount(n);
  });
});

test.describe("compare", () => {
  test.use({ scenario: { photos: "library", library: true, organize: "by-content-12" } });

  test("[CMP-001] 選 2–4 張比較：並排、縮放平移同步；第 5 張時說明原因", async ({ page }) => {
    await page.goto("/search?q=photo");
    const hits = page.getByTestId("search-result");
    await expect(hits.first()).toBeVisible();
    for (let i = 0; i < 5; i++) await hits.nth(i).click({ modifiers: ["Control"] });
    const cmp = page.getByRole("button", { name: "比較" });
    await expect(cmp).toHaveAttribute("aria-disabled", "true");
    await expect(page.getByText("最多比較 4 張")).toBeVisible();
    await hits.nth(4).click({ modifiers: ["Control"] }); // back to 4
    await expect(cmp).not.toHaveAttribute("aria-disabled", "true");
    await cmp.click();
    const view = page.getByRole("dialog", { name: "比較" });
    const panes = view.getByTestId("compare-pane");
    await expect(panes).toHaveCount(4);
    await view.getByRole("button", { name: "放大" }).click();
    const t = await panes.evaluateAll((els) => els.map((e) => (e.querySelector("img") as HTMLElement).style.transform));
    expect(new Set(t).size).toBe(1); // every pane has the same zoom and pan
    expect(t[0]).toContain("scale(1.5)");
  });

  test("[CMP-002] 比較模式鍵盤：1–4 切換主圖、Esc 離開並歸還焦點", async ({ page }) => {
    await page.goto("/search?q=photo");
    const hits = page.getByTestId("search-result");
    await expect(hits.first()).toBeVisible();
    await hits.nth(0).click({ modifiers: ["Control"] });
    await hits.nth(1).click({ modifiers: ["Control"] });
    const cmp = page.getByRole("button", { name: "比較" });
    await cmp.click();
    const view = page.getByRole("dialog", { name: "比較" });
    await expect(view).toBeFocused();
    await page.keyboard.press("2");
    await expect(view.getByTestId("compare-pane").nth(1)).toHaveAttribute("aria-current", "true");
    await page.keyboard.press("Escape");
    await expect(view).toHaveCount(0);
    await expect(cmp).toBeFocused();
  });
});
