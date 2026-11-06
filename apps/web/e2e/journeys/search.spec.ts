import type { Page, Request } from "@playwright/test";
import { expect, test } from "../fixtures";

// a scanned library organized by content: captions "a <category> photo", one tag per photo
test.use({ scenario: { photos: "library", library: true, organize: "by-content-12" } });

async function apiSearch(page: Page, body: Record<string, unknown>) {
  return (await (await page.request.post("/api/v1/search", { data: body })).json()) as { total: number; items: Array<{ matched_by: string[] }> };
}

test("[E2E-S-001] 關鍵字 + facet：結果數與 API 一致；chips 可移除", { tag: ["@critical"] }, async ({ page }) => {
  await page.goto("/");
  const box = page.getByRole("searchbox", { name: "搜尋照片" });
  await box.fill("photo");
  await box.press("Enter");
  await expect(page).toHaveURL(/\/search\?q=photo/);
  const count = page.getByTestId("result-count");
  const all = await apiSearch(page, { q: "photo" });
  await expect(count).toHaveText(`${all.total} 張`);
  // add a tag chip from the facet list
  const tagFacet = page.getByRole("group", { name: "標籤" }).getByRole("button").first();
  const tag = (await tagFacet.getAttribute("data-value"))!;
  await tagFacet.click();
  const withTag = await apiSearch(page, { q: "photo", filters: { tags_all: [tag] } });
  await expect(count).toHaveText(`${withTag.total} 張`);
  // and a year
  await page.getByRole("group", { name: "年份" }).getByRole("button", { name: /^2024/ }).click();
  const withYear = await apiSearch(page, { q: "photo", filters: { tags_all: [tag], taken_from: "2024-01-01", taken_to: "2024-12-31" } });
  await expect(count).toHaveText(`${withYear.total} 張`);
  const chips = page.getByRole("list", { name: "篩選條件" });
  await expect(chips.getByRole("listitem")).toHaveCount(2);
  await chips.getByRole("button", { name: `移除 ${tag}` }).click();
  await chips.getByRole("button", { name: "移除 2024" }).click();
  await expect(chips.getByRole("listitem")).toHaveCount(0);
  await expect(count).toHaveText(`${all.total} 張`);
});

test("[E2E-S-002] 以文搜圖：英文描述的結果標示 matched_by=image_vector", async ({ page }) => {
  await page.goto("/search?q=a%20cat%20sleeping%20on%20a%20sofa");
  const hits = page.getByTestId("search-result");
  await expect(hits.first()).toBeVisible({ timeout: 30_000 });
  const vec = hits.filter({ has: page.locator('[data-matched~="image_vector"]') });
  await expect(vec.first()).toBeVisible();
  await expect(vec.first().getByText("以文搜圖")).toBeVisible();
});

test("[E2E-S-003] 建索引中：橫幅「已可搜尋 X / Y 張」", async ({ page, request }) => {
  const r = await (await request.post("/__e2e/drop-embeddings", { data: { keep: 0.41 } })).json();
  await page.goto("/search?q=photo");
  await expect(page.getByTestId("index-banner")).toHaveText(`已可搜尋 ${r.searchable} / ${r.total} 張（以文搜圖）`);
});

test.describe("big library", () => {
  test.use({ scenario: { photos: "library", library: true, organize: "by-content-12" } });

  test("[E2E-SEL-001] ⌘A 選全部結果：「已選 3,214 張」，送出的是 query handle", async ({ page, request }) => {
    await request.post("/__e2e/bulk-library", { data: { n: 3214, tag: "beach" } });
    await page.goto("/search?q=beach");
    // the 30 library photos join through the vector top-K (ADR-0005); the 2022 facet narrows to the bulk set
    await page.getByRole("group", { name: "年份" }).getByRole("button", { name: /^2022/ }).click();
    await expect(page.getByTestId("result-count")).toHaveText("3,214 張");
    expect(await page.getByTestId("search-result").count()).toBeLessThan(3214); // only part is rendered
    let body: Record<string, unknown> | null = null;
    page.on("request", (r: Request) => {
      if (r.url().endsWith("/api/v1/selections") && r.method() === "POST") body = r.postDataJSON();
    });
    await page.getByTestId("search-result").first().click({ modifiers: ["Shift"] }); // focus the results without opening
    await page.keyboard.press("ControlOrMeta+a");
    await expect(page.getByTestId("batch-bar")).toContainText("已選 3,214 張");
    await page.getByTestId("batch-bar").getByRole("combobox", { name: "類別" }).selectOption({ label: "旅行" });
    await page.getByTestId("batch-bar").getByRole("button", { name: "套用類別" }).click();
    await expect(page.getByRole("status").filter({ hasText: "已更新 3,214 張" })).toBeVisible({ timeout: 60_000 });
    expect(body).not.toBeNull();
    expect(body!).toHaveProperty("query");
    expect(body!).not.toHaveProperty("asset_ids");
  });
});

test("[E2E-SEL-002] Shift 範圍選取 → 標籤、類別寫 review；「移到資料夾」產生計畫並導到核准頁", async ({ page, request }) => {
  await page.goto("/search?q=photo");
  const hits = page.getByTestId("search-result");
  await expect(hits.nth(9)).toBeVisible();
  await hits.nth(0).click({ modifiers: ["Shift"] });
  await hits.nth(9).click({ modifiers: ["Shift"] });
  const bar = page.getByTestId("batch-bar");
  await expect(bar).toContainText("已選 10 張");
  const ids = await hits.evaluateAll((els) => els.slice(0, 10).map((e) => e.getAttribute("data-asset")!));
  // tag
  await bar.getByRole("textbox", { name: "標籤" }).fill("家庭旅行");
  await bar.getByRole("button", { name: "加上標籤" }).click();
  await expect(page.getByRole("status").filter({ hasText: "已更新 10 張" })).toBeVisible();
  // category
  await bar.getByRole("combobox", { name: "類別" }).selectOption({ label: "動物" });
  await bar.getByRole("button", { name: "套用類別" }).click();
  await expect(page.getByRole("status").filter({ hasText: "已更新 10 張" })).toBeVisible();
  for (const id of ids) {
    await expect.poll(async () => (await (await request.get(`/api/v1/assets/${id}`)).json()).human_label?.category).toBe("pets");
  }
  // move to folder → a draft plan, then the approval page
  await bar.getByRole("button", { name: "移到資料夾…" }).click();
  const dlg = page.getByRole("dialog", { name: "移到資料夾" });
  await dlg.getByLabel("子資料夾").fill("精選");
  await dlg.getByRole("button", { name: "建立計畫" }).click();
  await expect(page).toHaveURL(/\/plans\//);
  await expect(page.getByTestId("plan-summary")).toHaveText(/^將複製 10 張到 1 個資料夾/);
});

test.describe("planner chips", () => {
  test.use({ scenario: { photos: "library", library: true } });

  test("[SRCH-PLAN-018] 自然語言日期變成可移除的 chip：結果數與 API 一致，移除後重跑", async ({ page, request }) => {
    await request.post("/__e2e/bulk-library", { data: { n: 3214, tag: "beach" } }); // hourly from 2022-01-01
    await page.goto(`/search?q=${encodeURIComponent("2022 年 3 月")}`);
    const chips = page.getByRole("list", { name: "篩選條件" });
    const date = chips.getByRole("listitem").filter({ hasText: "2022-03" });
    await expect(date).toBeVisible();
    const api = await (
      await request.post("/api/v1/search", { data: { q: "", filters: { taken_from: "2022-03-01", taken_to: "2022-04-01" }, limit: 1 } })
    ).json();
    expect(api.total).toBe(31 * 24); // March 2022, one photo per hour
    await expect(page.getByTestId("result-count")).toContainText(api.total.toLocaleString("en-US"));
    await date.getByRole("button").click(); // remove the chip: the query runs again without it
    await expect(date).toHaveCount(0);
    await expect(page.getByTestId("result-count")).not.toContainText(api.total.toLocaleString("en-US"));
  });
});
