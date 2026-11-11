import { expect, test } from "../fixtures";
import { snapshot, tree } from "../fsutil";

test("[E2E-B-001] 依日期端到端：選資料夾 → 依日期 → 預設輸出 → 預覽 → 核准 → 完成 → 復原", { tag: ["@critical"] }, async ({ page }) => {
  const t = tree();
  const photosBefore = snapshot(t.photos);
  const sortedBefore = snapshot(t.sorted);

  await page.goto("/organize"); // 04 §8.1: photos is the home page; organize has its own route
  // 1. choose a folder inside an allowed root
  await page.getByText("圖庫以外的資料夾…").click(); // the folder tree is folded on 整理 (ADR-0044)
  await page.getByRole("button", { name: "photos" }).click();
  await page.getByRole("button", { name: "trip", exact: true }).click();
  await page.getByRole("button", { name: "選擇此資料夾" }).click();
  // 2. method: by date
  await page.getByRole("radio", { name: /依日期/ }).check();
  // 3. default output location is the writable root
  await expect(page.getByTestId("output-target")).toHaveText("sorted");
  await page.getByRole("button", { name: "預覽" }).click();

  // 4. plan summary sentence
  const summary = page.getByTestId("plan-summary");
  await expect(summary).toHaveText(/^將複製 20 張到 \d+ 個資料夾，原檔不動。可隨時復原$/, { timeout: 30_000 });
  const folders = Number((await summary.textContent())!.match(/到 (\d+) 個/)![1]);

  // 5. approve -> completed
  await page.getByRole("button", { name: "核准並開始" }).click();
  await expect(page.getByTestId("done-title")).toHaveText("整理完成", { timeout: 30_000 });

  const after = snapshot(t.sorted);
  const files = Object.keys(after).filter((k) => after[k] !== "dir");
  expect(files).toHaveLength(20);
  for (const f of files) expect(f).toMatch(/^\d{4}\/\d{4}-\d{2}\/IMG_\d{4}\.jpg$/);
  expect(new Set(files.map((f) => f.split("/").slice(0, 2).join("/"))).size).toBe(folders);
  expect(snapshot(t.photos)).toEqual(photosBefore); // originals: content + mtime unchanged

  // 6. undo -> output folder back to the pre-apply state
  await page.getByRole("button", { name: "復原" }).click();
  await expect(page.getByTestId("undone-title")).toHaveText("已復原", { timeout: 30_000 });
  expect(snapshot(t.sorted)).toEqual(sortedBefore);
  expect(snapshot(t.photos)).toEqual(photosBefore);
});
