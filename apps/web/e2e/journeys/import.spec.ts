import { expect, test } from "../fixtures";

const shots = process.env.OPAGENT_E2E_SHOTS;

test("[IMP-014] 匯入：先看資料夾內容，加入後自動分析，完成後知道下一步，原檔不動", async ({ page }) => {
  await page.goto("/import");
  await page.getByRole("button", { name: "photos" }).click();
  await page.getByRole("button", { name: "trip", exact: true }).click();
  const pane = page.getByTestId("folder-preview");
  await expect(pane.getByText("張照片")).toBeVisible();
  await expect(pane.getByRole("img").first()).toBeVisible();
  await expect(pane).toContainText("不會修改");
  if (shots) await page.screenshot({ path: `${shots}/import.png` });
  await page.getByRole("button", { name: "加入圖庫並開始分析" }).click();
  await expect(page).toHaveURL(/\/$/);
  const dock = page.getByTestId("progress-dock");
  await expect(dock).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}/analyzing.png` });
  await expect(dock).toContainText("分析完成", { timeout: 30_000 });
  await expect(page.getByRole("gridcell").first()).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}/done.png` });
  await dock.getByRole("link", { name: "整理到資料夾" }).click();
  await expect(page).toHaveURL(/\/organize$/);
  const files = await (await page.request.get("/__e2e/tree")).json();
  expect(Object.keys(files).some((f) => f.startsWith("sorted/"))).toBe(false);
  await page.getByRole("link", { name: "分類" }).click();
  await expect(page.getByTestId("explore-summary")).toContainText("20");
  const chips = page.getByRole("region", { name: "分類" }).or(page.locator("section[aria-label='分類']")).getByRole("button");
  await chips.first().click();
  await expect(page.locator(".explore-tile").first()).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}/explore.png` });
});

test("[IMP-014] 圖庫是空的時候，照片頁直接帶去匯入", async ({ page }) => {
  await page.request.post("/api/v1/sources", { data: { root_alias: "sorted", sub_path: "" } });
  await page.goto("/");
  await page.getByRole("link", { name: "匯入照片" }).click();
  await expect(page).toHaveURL(/\/import$/);
});
