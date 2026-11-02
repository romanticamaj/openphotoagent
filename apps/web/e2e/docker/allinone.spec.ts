// Runs only in docker.yml against the published container (OPAGENT_E2E_BASE_URL); no test-stack fixtures here.
import { expect, test } from "@playwright/test";

test("[DOCK-001] Docker all-in-one：健康檢查通過，完成 onboarding 與一次依日期整理", async ({ page, request }) => {
  const h = await request.get("/healthz");
  expect(h.ok()).toBe(true);
  expect((await h.json()).app).toBe("openphotoagent");
  await page.goto("/");
  await expect(page).toHaveURL(/\/welcome/); // empty library starts with onboarding
  await page.goto("/organize");
  await page.getByRole("button", { name: "photos" }).click();
  await page.getByRole("button", { name: "trip", exact: true }).click();
  await page.getByRole("button", { name: "選擇此資料夾" }).click();
  await page.getByRole("radio", { name: /依日期/ }).check();
  await page.getByRole("button", { name: "預覽" }).click();
  await expect(page.getByTestId("plan-summary")).toHaveText(/^將複製 \d+ 張到 \d+ 個資料夾，原檔不動。可隨時復原$/, { timeout: 60_000 });
  await page.getByRole("button", { name: "核准並開始" }).click();
  await expect(page.getByTestId("done-title")).toHaveText("整理完成", { timeout: 60_000 });
});
