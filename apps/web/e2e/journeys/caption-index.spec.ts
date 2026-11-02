import { expect, test } from "../fixtures";

// SRCH-R2-004 (09b §7.5): the Models page shows the caption-vector index, its progress and ETA, and a switch that
// stays gated with a reason until the new index is complete (invariant 8).
test.describe("caption index", () => {
  test.use({ scenario: { photos: "trip", organize: "by-content-12" } });

  test("[SRCH-R2-004] 建立與切換 caption 向量索引：進度、預估時間、未完成前切換按鈕附原因", async ({ page }) => {
    await page.goto("/models");
    const box = page.getByRole("region", { name: "以文字描述找照片" });
    await box.getByRole("combobox", { name: "文字模型" }).selectOption("qwen3-embedding-0.6b");
    await box.getByRole("button", { name: "建立索引" }).click();
    await expect(box.getByTestId("caption-active")).toContainText("qwen3-embedding-0.6b");
    // a second model is built next to it; while it is building the switch explains why it is not available
    let hold = true;
    await page.route("**/api/v1/search/caption-index", async (route) => {
      if (route.request().method() !== "GET" || !hold) return route.continue();
      const real = await (await route.fetch()).json();
      if (real.building) real.building = { ...real.building, done: 0, ready: false };
      await route.fulfill({ json: real });
    });
    await box.getByRole("combobox", { name: "文字模型" }).selectOption("multilingual-e5-small");
    await box.getByRole("button", { name: "建立索引" }).click();
    const sw = box.getByRole("button", { name: "切換到新索引" });
    await expect(sw).toHaveAttribute("aria-disabled", "true");
    await expect(box.getByText("新索引還在建立中")).toBeVisible();
    await expect(box.getByRole("progressbar")).toBeVisible();
    hold = false;
    await page.unroute("**/api/v1/search/caption-index");
    await expect(sw).not.toHaveAttribute("aria-disabled", "true", { timeout: 15_000 });
    await sw.click();
    await expect(box.getByTestId("caption-active")).toContainText("multilingual-e5-small");
  });
});
