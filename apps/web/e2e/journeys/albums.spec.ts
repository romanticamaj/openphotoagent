import { expect, test } from "../fixtures";

// favourites and albums (ADR-0041): database sets; the photo files do not change
test.use({ scenario: { photos: "trip", library: true } });

test("[E2E-COL-001] ★ 收藏、加入新相簿、在相簿裡看照片並移除", { tag: ["@critical"] }, async ({ page }) => {
  await page.goto("/");
  await page.getByRole("gridcell").first().click();
  const lb = page.getByTestId("lightbox");
  await lb.getByRole("button", { name: "加入收藏" }).click();
  await expect(lb.getByRole("button", { name: "取消收藏" })).toHaveAttribute("aria-pressed", "true");
  await lb.getByRole("button", { name: "加入相簿" }).click();
  await lb.getByLabel("新相簿名稱").fill("旅行精選");
  await lb.getByRole("button", { name: "建立並加入" }).click();
  await expect(lb.getByRole("status")).toContainText("已加入「旅行精選」");
  await page.keyboard.press("Escape");
  await page.getByRole("link", { name: "相簿" }).click();
  const cards = page.getByTestId("album-list");
  await expect(cards).toContainText("收藏");
  await expect(cards).toContainText("旅行精選");
  await cards.getByRole("link", { name: /旅行精選/ }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("旅行精選");
  await page.getByRole("button", { name: "開啟照片" }).first().click();
  await expect(page.getByTestId("lightbox")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "從相簿移除" }).first().click({ force: true });
  await expect(page.getByText("還沒有照片")).toBeVisible();
});

test("[COL-008] Lightbox：在「問這張」打字不會觸發快捷鍵；按 F 收藏", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("gridcell").first().click();
  const lb = page.getByTestId("lightbox");
  await expect(lb.getByRole("button", { name: "加入收藏" })).toBeVisible(); // the photo is in
  await page.keyboard.press("f");
  await expect(lb.getByRole("button", { name: "取消收藏" })).toBeVisible();
  await page.keyboard.press("i");
  const field = lb.getByRole("textbox", { name: "問這張照片" });
  await field.fill("");
  await field.pressSequentially("fit");
  await expect(lb.getByTestId("info-panel")).toBeVisible(); // 「i」 typed into the field did not close it
  await expect(lb.getByRole("button", { name: "取消收藏" })).toBeVisible(); // nor did 「f」 undo the ★
});
