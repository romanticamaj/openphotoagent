import { expect, test } from "../fixtures";
import { snapshot, tree } from "../fsutil";

test("[TRASH-005] 重複照片一鍵計畫：選 20 組 → 保留代表張、其餘移到垃圾桶（可復原）→ 核准 → 復原", async ({ page, request }) => {
  const lib = await (await request.post("/__e2e/dup-library")).json(); // 20 photos × 2 locations on a writable root
  const before = snapshot(lib.dups);
  await page.goto("/duplicates");
  const groups = page.getByTestId("dup-group");
  await expect(groups).toHaveCount(20);
  await page.getByRole("checkbox", { name: "全選" }).check();
  await page.getByRole("button", { name: /產生清理計畫/ }).click();
  await expect(page.getByTestId("plan-summary")).toHaveText("將移到垃圾桶 20 張，保留每組的代表張。可隨時復原", { timeout: 30_000 });
  await page.getByRole("button", { name: "核准並開始" }).click();
  await expect(page.getByTestId("done-title")).toHaveText("整理完成", { timeout: 30_000 });
  const after = snapshot(lib.dups);
  const kept = Object.keys(after).filter((k) => after[k] !== "dir" && !k.startsWith(".opagent-trash/"));
  expect(kept).toHaveLength(20); // one location per photo remains
  const root = snapshot(tree().sorted); // the trash lives at the root: <root>/.opagent-trash/<plan>/dups/b/…
  expect(Object.keys(root).filter((k) => k.startsWith(".opagent-trash/") && root[k] !== "dir")).toHaveLength(20);
  await page.getByRole("button", { name: "復原" }).click();
  await expect(page.getByTestId("undone-title")).toHaveText("已復原", { timeout: 30_000 });
  const restored = snapshot(lib.dups);
  for (const [k, v] of Object.entries(before)) expect(restored[k]).toEqual(v); // every file back, bytes and mtime
});
