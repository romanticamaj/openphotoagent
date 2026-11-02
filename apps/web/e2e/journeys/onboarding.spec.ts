import type { Page } from "@playwright/test";
import { expect, test } from "../fixtures";

// fresh data dir: no sources, no models; the fake models "load" only when their downloads finish
const FORBIDDEN = /帳號|登入|Workspace|工作區|組織|Org\b/;

async function noAccountScreens(page: Page) {
  await expect(page.locator("body")).not.toContainText(FORBIDDEN);
}

test.describe("standard hardware", () => {
  test.use({ scenario: { hw: "rtx5070ti-win11", photos: "trip", models: "none" } });

  test("[E2E-A-001] 快樂路徑：歡迎 → 硬體一句話 → 一個推薦組合 → 開始 → 下載中選資料夾 → 拼貼牆", { tag: ["@critical"] }, async ({ page, request }) => {
    const t0 = Date.now();
    await page.goto("/");
    await expect(page).toHaveURL(/\/welcome$/); // empty library → onboarding
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("歡迎使用 OpenPhotoAgent");
    await noAccountScreens(page);
    await page.getByRole("button", { name: "開始" }).click();

    await expect(page.getByTestId("hw-conclusion")).toHaveText(/^你的電腦可以跑標準模式（.*RTX 5070 Ti.* · 16 GB）$/);
    const plans = page.getByTestId("plan-card");
    await expect(plans).toHaveCount(1); // one recommended combination
    await expect(plans).toContainText(/需要下載 [\d.]+ (MB|GB)，約 [\d.]+ 分鐘/);
    await noAccountScreens(page);
    // the search bar says why it is unavailable until SigLIP2 is ready (invariant 8)
    const search = page.getByRole("searchbox", { name: "搜尋照片" });
    await expect(search).toBeDisabled();
    await expect(search).toHaveAccessibleDescription(/尚未安裝|下載中/);

    await page.getByRole("button", { name: "開始下載" }).click();
    // downloads run in the background while the folder is chosen
    await expect(page.getByRole("heading", { name: "選擇照片資料夾" })).toBeVisible();
    await expect(page.getByTestId("download-list")).toContainText("SigLIP");

    // SigLIP2 first: search becomes available while the VLM is still downloading
    await expect(search).toBeEnabled({ timeout: 30_000 });
    const ops = await (await request.get("/api/v1/operations?status=all&type=model.download")).json();
    const vlm = ops.find((o: { subject: { id: string } }) => o.subject.id.startsWith("qwen3-vl"));
    expect(vlm.status).not.toBe("succeeded");

    await page.getByRole("button", { name: "photos" }).click();
    await page.getByRole("button", { name: "trip", exact: true }).click();
    await page.getByRole("button", { name: "選擇此資料夾" }).click();
    const wall = page.getByTestId("scan-collage");
    await expect(wall.locator("img")).toHaveCount(20, { timeout: 60_000 });
    await noAccountScreens(page);
    expect(Date.now() - t0).toBeLessThan(5 * 60_000);
  });

  test("[E2E-A-005] 授權免勾選：預設組合只有一行「模型授權：Apache-2.0」", async ({ page }) => {
    await page.goto("/welcome");
    await page.getByRole("button", { name: "開始" }).click();
    await expect(page.getByTestId("license-line")).toHaveText("模型授權：Apache-2.0");
    await expect(page.getByRole("checkbox")).toHaveCount(0);
  });
});

test.describe("old NVIDIA driver", () => {
  test.use({ scenario: { hw: "nvidia-drv535", photos: "trip", models: "none" } });

  test("[E2E-A-002] 驅動過舊不卡住：「開啟驅動下載頁」與「先用 Vulkan 繼續」，選後者可完成一次整理", async ({ page }) => {
    await page.goto("/welcome");
    await page.getByRole("button", { name: "開始" }).click();
    const card = page.getByRole("group", { name: /驅動/ });
    await expect(card.getByRole("link", { name: "開啟驅動下載頁" })).toHaveAttribute("href", /^https:\/\/.*nvidia/);
    await card.getByRole("button", { name: "先用 Vulkan 繼續" }).click();
    await expect(page.getByTestId("plan-card")).toContainText("Vulkan");
    await page.getByRole("button", { name: "開始下載" }).click();
    await page.getByRole("button", { name: "photos" }).click();
    await page.getByRole("button", { name: "trip", exact: true }).click();
    await page.getByRole("button", { name: "選擇此資料夾" }).click();
    await expect(page.getByTestId("scan-collage").locator("img")).toHaveCount(20, { timeout: 60_000 });
    // one organize run to completion
    await page.getByRole("link", { name: "整理這些照片" }).click();
    await page.getByRole("button", { name: "photos" }).click();
    await page.getByRole("button", { name: "trip", exact: true }).click();
    await page.getByRole("button", { name: "選擇此資料夾" }).click();
    await page.getByRole("radio", { name: /依日期/ }).check();
    await page.getByRole("button", { name: "預覽" }).click();
    await page.getByRole("button", { name: "核准並開始" }).click();
    await expect(page.getByTestId("done-title")).toHaveText("整理完成", { timeout: 30_000 });
  });
});

test.describe("no GPU", () => {
  test.use({ scenario: { hw: "no-gpu-linux", photos: "trip", models: "none" } });

  test("[E2E-A-003] 無 GPU：推薦「快速模式」並明示預估時間", async ({ page }) => {
    await page.goto("/welcome");
    await page.getByRole("button", { name: "開始" }).click();
    await expect(page.getByTestId("hw-conclusion")).toContainText("快速模式");
    const plan = page.getByTestId("plan-card");
    await expect(plan).toContainText("快速模式");
    await expect(plan.getByTestId("per-1000")).toHaveText(/^整理 1,000 張約 [\d.]+–[\d.]+ 分鐘（估算）$/);
  });
});

test.describe("offline", () => {
  test.use({ scenario: { hw: "rtx5070ti-win11", photos: "trip", models: "none", net: "off" } });

  test("[E2E-A-004] 離線安裝：NET_UNREACHABLE 卡片附鏡像／代理 CTA；「依日期」仍可完成", async ({ page }) => {
    await page.goto("/welcome");
    await page.getByRole("button", { name: "開始" }).click();
    await page.getByRole("button", { name: "開始下載" }).click();
    const card = page.getByRole("group", { name: /無法連線/ });
    await expect(card).toBeVisible({ timeout: 30_000 });
    await expect(card.getByRole("button", { name: "重試" })).toBeVisible();
    await expect(card.getByRole("button", { name: "設定鏡像" })).toBeVisible();
    await expect(card.getByRole("button", { name: "設定代理" })).toBeVisible();
    // organizing by date needs no model
    await page.goto("/organize");
    await page.getByRole("button", { name: "photos" }).click();
    await page.getByRole("button", { name: "trip", exact: true }).click();
    await page.getByRole("button", { name: "選擇此資料夾" }).click();
    await page.getByRole("radio", { name: /依日期/ }).check();
    await page.getByRole("button", { name: "預覽" }).click();
    await page.getByRole("button", { name: "核准並開始" }).click();
    await expect(page.getByTestId("done-title")).toHaveText("整理完成", { timeout: 30_000 });
  });
});
