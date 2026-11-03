// The main journeys with the keyboard only (09b §9.3 A11Y-011; WCAG 2.1.1, 2.4.7): every control is reached with
// Tab — never clicked — has a visible focus indicator when reached, and is activated with Enter/Space.
import type { Locator, Page } from "@playwright/test";
import { expect, test } from "../fixtures";
import { snapshot, tree } from "../fsutil";

/** Tab until `target` has focus (at most `max` presses), check its focus ring, and return how many presses it took. */
async function tabTo(page: Page, target: Locator, max = 80): Promise<number> {
  await target.waitFor();
  for (let i = 1; i <= max; i++) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((el) => el === document.activeElement || el.contains(document.activeElement))) {
      const ring = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement;
        const cs = getComputedStyle(el);
        return (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || cs.boxShadow !== "none";
      });
      expect(ring, "focus must be visible").toBe(true);
      return i;
    }
  }
  throw new Error(`could not reach ${target} with Tab`);
}

async function press(page: Page, target: Locator, key = "Enter") {
  await tabTo(page, target);
  await page.keyboard.press(key);
}

test.describe("keyboard only", () => {
  test("[A11Y-011] 只用鍵盤：選資料夾 → 依日期 → 預覽 → 核准 → 完成 → 復原", async ({ page }) => {
    const t = tree();
    const photosBefore = snapshot(t.photos);
    await page.goto("/organize");
    await press(page, page.getByRole("button", { name: "photos" }));
    await press(page, page.getByRole("button", { name: "trip", exact: true }));
    await press(page, page.getByRole("button", { name: "選擇此資料夾" }));
    await press(page, page.getByRole("radio", { name: /依日期/ }), "Space");
    await expect(page.getByRole("radio", { name: /依日期/ })).toBeChecked();
    await press(page, page.getByRole("button", { name: "預覽" }));
    await expect(page.getByTestId("plan-summary")).toHaveText(/^將複製 20 張/, { timeout: 30_000 });
    await press(page, page.getByRole("button", { name: "核准並開始" }));
    await expect(page.getByTestId("done-title")).toHaveText("整理完成", { timeout: 30_000 });
    await press(page, page.getByRole("button", { name: "復原" }));
    await expect(page.getByTestId("undone-title")).toHaveText("已復原", { timeout: 30_000 });
    expect(snapshot(t.photos)).toEqual(photosBefore);
  });

  test.describe("search", () => {
    test.use({ scenario: { photos: "library", library: true, organize: "by-content-12" } });

    test("[A11Y-011] 只用鍵盤：搜尋 → 結果 → Lightbox 左右切換 → Esc 歸還焦點", async ({ page }) => {
      await page.goto("/");
      const box = page.getByRole("searchbox").or(page.getByRole("combobox")).first();
      await tabTo(page, box);
      await page.keyboard.type("photo");
      await page.keyboard.press("Enter");
      const first = page.getByTestId("search-result").first();
      await press(page, first);
      const lb = page.getByTestId("lightbox");
      await expect(lb).toBeVisible();
      const pos = page.getByTestId("lightbox-position");
      const before = await pos.textContent();
      await page.keyboard.press("ArrowRight");
      await expect(pos).not.toHaveText(before ?? "");
      await page.keyboard.press("Escape");
      await expect(lb).toBeHidden();
      await expect(first).toBeFocused(); // focus goes back where it came from
    });
  });

  test.describe("ask", () => {
    test.use({ scenario: { photos: "trip", library: true } });

    test("[A11Y-011] 只用鍵盤：⌘I／Ctrl+I 開問 AI → 輸入 → 結果 → Esc 關閉並歸還焦點", async ({ page }) => {
      await page.goto("/");
      const opener = page.getByRole("button", { name: "✦ 問 AI" });
      await tabTo(page, opener);
      await page.keyboard.press("Control+i");
      const panel = page.getByRole("complementary", { name: "問你的照片" });
      await expect(panel).toBeVisible();
      const input = panel.getByRole("textbox", { name: "問點什麼" });
      await expect(input).toBeFocused(); // the panel puts the caret where you type
      await page.keyboard.type("2023 年的照片");
      await page.keyboard.press("Enter");
      await expect(panel.getByTestId("ask-photos-total")).toHaveText("找到 10 張");
      await page.keyboard.press("Escape");
      await expect(panel).toBeHidden();
      await expect(opener).toBeFocused();
    });
  });
});
