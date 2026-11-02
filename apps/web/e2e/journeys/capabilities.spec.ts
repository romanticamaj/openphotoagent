import { expect, test } from "../fixtures";

test.use({ scenario: { photos: "library", library: true } });

test("[DESK-032] capabilities 驅動 UI：single_user=true 時沒有 Org／Workspace 選單、沒有 Workers 頁", async ({ page }) => {
  const caps = page.waitForResponse((r) => r.url().endsWith("/api/v1/capabilities"));
  await page.goto("/");
  expect((await (await caps).json()).single_user).toBe(true);
  await expect(page.locator(".shell")).toHaveAttribute("data-single-user", "true");
  const nav = page.getByRole("navigation", { name: "主要導覽" });
  await expect(nav.getByRole("link")).not.toHaveCount(0);
  for (const name of [/workers?/i, /本機引擎/, /workspace/i, /工作區/, /organi[sz]ation/i, /組織/]) {
    await expect(page.getByRole("link", { name })).toHaveCount(0);
    await expect(page.getByRole("combobox", { name })).toHaveCount(0);
  }
  await page.goto("/system/workers");
  await expect(page.getByRole("heading", { name: /workers/i })).toHaveCount(0);
});
