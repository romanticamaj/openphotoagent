import { expect, test } from "../fixtures";

// 「AI 問照片」 (10 §9): the side panel streams steps with their times, chips can be edited and re-run without
// re-parsing, cloud mode is always visible, and a machine without a GPU gets a way forward instead of an error.
test.describe("ask", () => {
  test.use({ scenario: { photos: "trip", library: true } });

  test("[ASK-SSE-004] 編輯條件 chips 後重跑：不重新解析問題，結果跟著變", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "✦ 問 AI" }).click();
    const panel = page.getByRole("complementary", { name: "問你的照片" });
    await panel.getByRole("textbox", { name: "問點什麼" }).fill("2023 年的照片");
    await panel.getByRole("textbox", { name: "問點什麼" }).press("Enter");
    await expect(panel.getByTestId("ask-photos-total")).toHaveText("找到 10 張");
    const steps = panel.getByTestId("ask-step");
    await expect(steps.first()).toContainText("理解問題");
    await expect(steps.nth(1)).toContainText(/搜尋照片.*\d+\.\d s/); // every tool step shows its time
    const chip = panel.getByRole("button", { name: /移除條件：日期/ });
    await expect(chip).toBeVisible();
    const sent: unknown[] = [];
    page.on("request", (r) => {
      if (r.url().endsWith("/api/v1/ask")) sent.push(r.postDataJSON());
    });
    await chip.click();
    await panel.getByRole("button", { name: "以這些條件重新執行" }).click();
    await expect(panel.getByTestId("ask-photos-total").last()).toHaveText("找到 20 張");
    await expect(panel.getByTestId("ask-step").filter({ hasText: "使用你編輯的條件" })).toHaveCount(1);
    expect(sent.at(-1)).toMatchObject({ message: "2023 年的照片", filters: {} });
  });

  test("[ASK-UI-001] 空白面板顯示依圖庫資料產生的建議問題，點一下就問", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "✦ 問 AI" }).click();
    const panel = page.getByRole("complementary", { name: "問你的照片" });
    const chips = panel.getByRole("list", { name: "建議問題" }).getByRole("button");
    await expect(chips.filter({ hasText: "2024 年的照片" })).toHaveCount(1);
    await chips.filter({ hasText: "2024 年的照片" }).click();
    await expect(panel.getByTestId("ask-photos-total")).toHaveText("找到 10 張");
  });

  test("[ASK-PRV-001] 設定外部 endpoint 後，面板常駐雲端模式警示", async ({ page }) => {
    await page.goto("/settings/assistant");
    await page.getByRole("radio", { name: /外部 OpenAI 相容服務/ }).check();
    await page.getByRole("textbox", { name: "服務網址" }).fill("https://llm.example/v1");
    await page.getByRole("button", { name: "儲存" }).click();
    await expect(page.getByTestId("assistant-saved")).toBeVisible();
    await page.goto("/");
    await page.getByRole("button", { name: "✦ 問 AI" }).click();
    const panel = page.getByRole("complementary", { name: "問你的照片" });
    await expect(panel.getByTestId("ask-cloud-warning")).toContainText("照片影像不會送出");
    await expect(panel.getByTestId("ask-mode")).toHaveText("雲端");
  });
});

test.describe("ask on a machine without a GPU", () => {
  test.use({ scenario: { photos: "trip", library: true, hw: "no-gpu-linux" } });

  test("[ASK-PRV-005] 沒有 GPU：說明需要外部 endpoint 或較慢的本地模式，並給設定 CTA", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "✦ 問 AI" }).click();
    const panel = page.getByRole("complementary", { name: "問你的照片" });
    const advice = panel.getByTestId("ask-cpu-advice");
    await expect(advice).toContainText("外部服務");
    await expect(advice).toContainText("較慢的本地模式");
    await advice.getByRole("link", { name: "設定外部服務" }).click();
    await expect(page).toHaveURL(/\/settings\/assistant$/);
    await page.goBack();
    await panel.getByTestId("ask-cpu-advice").getByRole("button", { name: "使用較慢的本地模式" }).click();
    await expect(panel.getByTestId("ask-cpu-advice")).toHaveCount(0);
    await expect(panel.getByRole("textbox", { name: "問點什麼" })).toBeEnabled();
  });
});

test.describe("ask about one photo", () => {
  test.use({ scenario: { photos: "trip", library: true } });

  test("[ASK-UI-001] Lightbox「問這張」：建議問題用 metadata 回答", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("gridcell").first().click();
    const lb = page.getByTestId("lightbox");
    await lb.getByRole("button", { name: "資訊" }).click();
    const box = lb.getByRole("region", { name: "問這張" });
    await box.getByRole("button", { name: "這張是什麼時候拍的？" }).click();
    await expect(box.getByTestId("ask-answer")).toContainText(/拍攝於 20\d\d 年 \d+ 月 \d+ 日/);
  });
});
