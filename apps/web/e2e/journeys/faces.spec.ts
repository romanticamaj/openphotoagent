import { expect, test } from "../fixtures";

// Faces (07 §4): off by default and never blocking, a review loop by keyboard, and disabling deletes by default.
test.describe("faces", () => {
  test.use({ scenario: { photos: "trip", library: true } });

  test("[FACE-GATE-004] 人臉卡片可按「稍後」略過；之後可在設定開啟；未開啟時沒有「人物」導覽", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("link", { name: "人物" })).toHaveCount(0);
    const card = page.getByTestId("faces-optin-card");
    await expect(card).toBeVisible();
    await card.getByRole("button", { name: "稍後" }).click();
    await expect(card).toHaveCount(0);
    await page.reload();
    await expect(page.getByTestId("faces-optin-card")).toHaveCount(0); // stays dismissed, never blocks
    await expect(page.getByRole("gridcell").first()).toBeVisible();
    await page.goto("/settings/faces");
    await page.getByRole("button", { name: "開啟人臉辨識" }).click();
    const wiz = page.getByRole("dialog", { name: "開啟人臉辨識" });
    const go = wiz.getByRole("button", { name: "同意並開啟" });
    await expect(go).toHaveAttribute("aria-disabled", "true"); // consent first
    await wiz.getByRole("checkbox", { name: /我了解/ }).check();
    await go.click();
    await expect(page.getByRole("link", { name: "人物" })).toBeVisible();
  });

  test("[FACE-REV-001] 「這是不是小明？」：Y／N／S 鍵盤連續處理，依分數由低到高", async ({ page, request }) => {
    const lib = await (await request.post("/__e2e/face-library")).json();
    await page.goto("/people/review");
    const q = page.getByTestId("face-question");
    await expect(q).toContainText("這是不是 小明？");
    await expect(page.getByTestId("face-score")).toHaveText("0.50"); // least certain first
    await page.keyboard.press("y");
    await expect(page.getByTestId("face-score")).toHaveText("0.51");
    await page.keyboard.press("n");
    await expect(page.getByTestId("face-score")).toHaveText("0.52");
    await page.keyboard.press("s");
    await expect(page.getByTestId("face-review-done")).toBeVisible();
    const states = await (await request.get(`/api/v1/faces/review`)).json();
    expect(states.map((x: { face_id: string }) => x.face_id)).toEqual([lib.suggestions[2]]); // skipped stays
  });

  test("[FACE-PRG-004] 停用人臉時預設勾選「同時刪除」，確認後全部刪除", async ({ page, request }) => {
    await request.post("/__e2e/face-library");
    await page.goto("/settings/faces");
    await page.getByRole("button", { name: "停用人臉辨識" }).click();
    const dlg = page.getByRole("dialog", { name: "停用人臉辨識" });
    await expect(dlg.getByRole("checkbox", { name: /同時刪除/ })).toBeChecked();
    await dlg.getByRole("textbox", { name: /輸入圖庫名稱/ }).fill("Default");
    await dlg.getByRole("button", { name: "停用並刪除" }).click();
    await expect(page.getByTestId("purge-report")).toContainText("16");
    expect((await (await request.get("/api/v1/people")).json()).people).toEqual([]);
  });
});
