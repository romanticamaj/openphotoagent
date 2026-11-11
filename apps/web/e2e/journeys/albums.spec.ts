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
  await page.getByRole("button", { name: /^照片，/ }).first().click();
  await expect(page.getByTestId("lightbox")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "從相簿移除" }).first().click({ force: true });
  await expect(page.getByText("還沒有照片")).toBeVisible();
  await page.getByRole("button", { name: "復原" }).click(); // back in the album
  await expect(page.getByRole("button", { name: /^照片，/ })).toHaveCount(1);
  await page.getByRole("button", { name: "刪除相簿" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("照片不會被刪除");
  await page.getByRole("button", { name: "確定刪除" }).click();
  await expect(page.getByTestId("album-list")).not.toContainText("旅行精選");
});

test("[COL-010] 相簿裡的照片排成格子，不疊在一起", async ({ page }) => {
  await page.goto("/");
  for (const i of [0, 1, 2]) {
    await page.getByRole("gridcell").nth(i).click();
    const lb = page.getByTestId("lightbox");
    await expect(lb.getByRole("button", { name: "加入收藏" })).toBeVisible();
    await lb.getByRole("button", { name: "加入收藏" }).click();
    await expect(lb.getByRole("button", { name: "取消收藏" })).toBeVisible();
    await page.keyboard.press("Escape");
  }
  await page.goto("/albums/fav-default");
  const tiles = page.locator(".album-tile");
  await expect(tiles).toHaveCount(3);
  const boxes = await tiles.evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).map((r) => `${Math.round(r.x)},${Math.round(r.y)}`));
  expect(new Set(boxes).size).toBe(3);
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

test("[VIEW-038] 時間軸多選：Ctrl 點選、Shift 範圍、選取時點一下也是選；一次加入收藏；Esc 取消（驗證第 50 輪）", async ({ page, request }) => {
  await page.goto("/");
  const tiles = page.getByRole("grid", { name: "照片網格" }).getByRole("gridcell");
  await tiles.nth(0).click({ modifiers: ["ControlOrMeta"] });
  await expect(page.getByTestId("lightbox")).toHaveCount(0); // picked, not opened
  await expect(tiles.nth(0)).toHaveAttribute("aria-selected", "true");
  await tiles.nth(2).click({ modifiers: ["Shift"] });
  const bar = page.getByTestId("timeline-batch");
  await expect(bar).toContainText("已選 3 張");
  await tiles.nth(1).click(); // while choosing, a click chooses
  await expect(bar).toContainText("已選 2 張");
  await tiles.nth(1).focus();
  await page.keyboard.press(" ");
  await expect(bar).toContainText("已選 3 張");
  await bar.getByRole("button", { name: "加入收藏" }).click();
  await expect(bar.getByRole("status")).toContainText("已加入「收藏」");
  const fav = await (await request.get("/api/v1/albums/fav-default", { headers: { "X-OPA-UI": "1" } })).json();
  expect(fav.photos).toHaveLength(3);
  await page.keyboard.press("Escape");
  await expect(bar).toHaveCount(0);
  await tiles.nth(0).click();
  await expect(page.getByTestId("lightbox")).toBeVisible(); // nothing chosen: a click opens again
});

