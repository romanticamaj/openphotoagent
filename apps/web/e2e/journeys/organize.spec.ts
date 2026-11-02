import { mkdirSync, renameSync, writeFileSync, copyFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "../fixtures";
import { snapshot, tree } from "../fsutil";

async function pickTrip(page: Page) {
  await page.goto("/organize");
  await page.getByRole("button", { name: "photos" }).click();
  await page.getByRole("button", { name: "trip", exact: true }).click();
  await page.getByRole("button", { name: "選擇此資料夾" }).click();
}

async function backendCalls(page: Page): Promise<number> {
  return (await (await page.request.get("/__e2e/stats")).json()).vlm_requests + 0;
}

test("[E2E-B-002] 依內容 + 只改門檻：預覽 < 200 ms 更新，不重新推論，顯示全庫分布與總時間", { tag: ["@critical"] }, async ({ page }) => {
  await pickTrip(page);
  await page.getByRole("radio", { name: /依內容/ }).check();
  await page.getByRole("button", { name: "預覽" }).click();
  const cards = page.getByTestId("sample-card");
  await expect(cards).toHaveCount(10, { timeout: 60_000 });
  const dist = page.getByTestId("library-distribution");
  await expect(dist).toContainText(/張/, { timeout: 60_000 });
  await expect(page.getByTestId("library-time")).toHaveText(/^預估總時間 約 .+$/);
  const before = await backendCalls(page);
  const decisions = async () => (await cards.evaluateAll((els) => els.map((e) => e.getAttribute("data-decision")))).join(",");
  const was = await decisions();
  const slider = page.getByRole("slider", { name: "自動採用門檻" });
  await slider.focus();
  const t0 = await page.evaluate(() => performance.now());
  await slider.press("End"); // accept threshold → 1.0: nothing auto-accepted any more
  await expect.poll(decisions, { timeout: 2000 }).not.toBe(was);
  const dt = (await page.evaluate(() => performance.now())) - t0;
  expect(dt).toBeLessThan(200 + 100); // budget + one poll interval of the assertion
  expect(await backendCalls(page)).toBe(before); // thresholds never re-run inference (invariant 7)
});

test.describe("custom categories", () => {
  test.use({ scenario: { photos: "trip" } });
  test("[E2E-B-003] 自訂類別：新增「貓咪」+ 描述 + 拖 3 張照片 → 全庫預估命中數；預覽中出現", async ({ page }) => {
    await pickTrip(page);
    await page.getByRole("radio", { name: /自訂類別/ }).check();
    await page.getByRole("button", { name: "新增類別" }).click();
    await page.getByLabel("類別名稱", { exact: true }).fill("貓咪");
    await page.getByLabel("描述", { exact: true }).fill("家裡養的貓");
    await page.getByRole("button", { name: "預覽" }).click();
    const cards = page.getByTestId("sample-card");
    await expect(cards).toHaveCount(10, { timeout: 60_000 });
    const target = page.getByTestId("category-card").filter({ hasText: "貓咪" });
    for (let i = 0; i < 3; i++) await cards.nth(i).dragTo(target);
    await expect(target.getByTestId("example")).toHaveCount(3);
    await expect(target.getByTestId("library-hits")).toHaveText(/^全庫預估 \d[\d,]* 張$/, { timeout: 60_000 });
    await expect(cards.filter({ hasText: "貓咪" }).first()).toBeVisible({ timeout: 60_000 });
  });
});

test.describe("reviews", () => {
  test.use({ scenario: { photos: "trip", vlm_p: 0.6 } }); // mid confidence → everything needs review

  test("[E2E-B-004] 待確認與逃生門：鍵盤處理 3 張 →「略過剩下的…」→ 其餘進「其他」，可繼續到計畫", { tag: ["@critical"] }, async ({ page }) => {
    await pickTrip(page);
    await page.getByRole("radio", { name: /依內容/ }).check();
    await page.getByRole("button", { name: "建立整理計畫" }).click();
    const banner = page.getByTestId("review-banner");
    await expect(banner).toContainText(/有 \d+ 張待確認/, { timeout: 60_000 });
    const pending = Number((await banner.textContent())!.match(/有 (\d+) 張/)![1]);
    await banner.getByRole("link", { name: "開始確認" }).click();
    const counter = page.getByTestId("review-remaining");
    await expect(counter).toHaveText(`剩 ${pending} 張`);
    await page.keyboard.press("Enter"); // accept the suggestion
    await expect(counter).toHaveText(`剩 ${pending - 1} 張`);
    await page.keyboard.press("2"); // second candidate
    await expect(counter).toHaveText(`剩 ${pending - 2} 張`);
    await page.keyboard.press("0"); // other
    await expect(counter).toHaveText(`剩 ${pending - 3} 張`);
    await page.getByRole("button", { name: /^略過剩下的/ }).click();
    await page.getByRole("dialog").getByRole("button", { name: "全部放到「其他」" }).click();
    await expect(page.getByTestId("plan-summary")).toBeVisible({ timeout: 30_000 });
    const plan = await (await page.request.get(`/api/v1/output-plans/${page.url().split("/plans/")[1]}`)).json();
    const other = plan.actions.filter((a: { dst_rel: string }) => a.dst_rel.startsWith("其他/")).length;
    expect(other).toBeGreaterThanOrEqual(pending - 3);
  });

  test("[REV-002] 一次一張與數字鍵：1–9、0、Enter、S；處理後自動下一張", async ({ page, request, stack }) => {
    const j = await (
      await request.post("/api/v1/jobs", {
        data: { kind: "organize", preset: "by-content-12", source_id: (await (await request.post("/api/v1/sources", { data: { root_alias: "photos", sub_path: "trip" } })).json()).id, target: { root_alias: "sorted", sub_path: "" } },
      })
    ).json();
    void stack;
    await expect.poll(async () => (await (await request.get(`/api/v1/reviews?job_id=${j.id}`)).json()).items.length, { timeout: 60_000 }).toBeGreaterThan(4);
    await page.goto(`/review?job=${j.id}`);
    const img = page.getByTestId("review-photo");
    const ids: string[] = [];
    for (const key of ["1", "0", "Enter", "s"]) {
      ids.push((await img.getAttribute("data-asset"))!);
      await page.keyboard.press(key);
      await expect(img).not.toHaveAttribute("data-asset", ids[ids.length - 1]); // moved on automatically
    }
    type R = { asset_id: string; status: string; final: { category: string } | null };
    const read = async () => Object.fromEntries(((await (await request.get(`/api/v1/reviews?job_id=${j.id}&status=all`)).json()).items as R[]).map((r) => [r.asset_id, r]));
    await expect.poll(async () => {
      const now = await read();
      return ids.map((id) => (now[id] as R).status);
    }).not.toContain("pending");
    const by = await read();
    expect(by[ids[0]].status).toBe("corrected");
    expect(by[ids[1]].final!.category).toBe("other");
    expect(by[ids[2]].status).toBe("approved");
    expect(by[ids[3]].status).toBe("skipped");
  });

  test("[REV-005] 執行中即可覆核：已產生的 review 可處理，新的即時加入", async ({ page, request }) => {
    await request.post("/__e2e/slow", { data: { vlm_latency_s: 0.4 } });
    const src = (await (await request.post("/api/v1/sources", { data: { root_alias: "photos", sub_path: "trip" } })).json()).id;
    const j = await (await request.post("/api/v1/jobs", { data: { kind: "organize", preset: "by-content-12", source_id: src, target: { root_alias: "sorted", sub_path: "" } } })).json();
    await expect.poll(async () => (await (await request.get(`/api/v1/reviews?job_id=${j.id}`)).json()).items.length, { timeout: 60_000 }).toBeGreaterThan(0);
    expect((await (await request.get(`/api/v1/jobs/${j.id}`)).json()).status).toBe("running");
    await page.goto(`/review?job=${j.id}`);
    const counter = page.getByTestId("review-remaining");
    const first = Number((await counter.textContent())!.match(/\d+/)![0]);
    await page.keyboard.press("Enter");
    await expect.poll(async () => Number((await counter.textContent())!.match(/\d+/)![0]), { timeout: 30_000 }).toBeGreaterThan(first - 1);
  });
});

test("[E2E-B-005] 移動需二次確認；> 10,000 move 時要求輸入確認字串", async ({ page, request }) => {
  // the sorted root is writable: put the trip photos there and move them into date folders
  const t = tree();
  await request.post("/__e2e/move-into-sorted");
  await page.goto("/organize");
  await page.getByRole("button", { name: "sorted" }).first().click();
  await page.getByRole("button", { name: "inbox", exact: true }).click();
  await page.getByRole("button", { name: "選擇此資料夾" }).click();
  await page.getByRole("radio", { name: /依日期/ }).check();
  await page.getByText("進階").click();
  await page.getByRole("checkbox", { name: /移動原檔/ }).check();
  await page.getByRole("button", { name: "預覽" }).click();
  await page.getByRole("button", { name: "核准並開始" }).click();
  const dialog = page.getByRole("dialog", { name: "確認移動" });
  await expect(dialog).toBeVisible();
  const go = dialog.getByRole("button", { name: "移動" });
  await expect(go).toBeDisabled();
  await expect(go).toHaveAccessibleDescription(/勾選/);
  await dialog.getByRole("checkbox", { name: /我了解原檔會被移走/ }).check();
  await go.click();
  await expect(page.getByTestId("done-title")).toHaveText("整理完成", { timeout: 30_000 });
  expect(Object.keys(snapshot(join(t.sorted, "inbox"))).filter((k) => k.endsWith(".jpg"))).toEqual([]);

  // > 10,000 moves: the confirmation string is required (backend fixture plan)
  const big = await (await request.post("/__e2e/big-move-plan", { data: { n: 10_001 } })).json();
  await page.goto(`/plans/${big.plan_id}`);
  await page.getByRole("button", { name: "核准並開始" }).click();
  const d2 = page.getByRole("dialog", { name: "確認移動" });
  await d2.getByRole("checkbox", { name: /我了解原檔會被移走/ }).check();
  const go2 = d2.getByRole("button", { name: "移動" });
  await expect(go2).toBeDisabled();
  await d2.getByLabel(/輸入/).fill("MOVE 10001");
  await expect(go2).toBeEnabled();
});

test("[E2E-B-006] 衝突自動處理：2 個重名已加後綴、1 個相同已略過；細節樹可展開", async ({ page }) => {
  const t = tree();
  await pickTrip(page);
  await page.getByRole("radio", { name: /依日期/ }).check();
  await page.getByRole("button", { name: "預覽" }).click();
  await expect(page.getByTestId("plan-summary")).toBeVisible({ timeout: 30_000 });
  const plan = await (await page.request.get(`/api/v1/output-plans/${page.url().split("/plans/")[1]}`)).json();
  const dst = Object.fromEntries(plan.actions.map((a: { src_rel: string; dst_rel: string }) => [a.src_rel, a.dst_rel]));
  for (const [i, body] of [["01", "not the same photo"], ["02", "another one"]]) {
    const p = join(t.sorted, dst[`trip/IMG_00${i}.jpg`]);
    mkdirSync(dirname(p), { recursive: true });
    writeFileSync(p, body);
  }
  const same = join(t.sorted, dst["trip/IMG_0003.jpg"]);
  mkdirSync(dirname(same), { recursive: true });
  copyFileSync(join(t.photos, "trip/IMG_0003.jpg"), same);
  await page.getByRole("button", { name: "核准並開始" }).click();
  await expect(page.getByTestId("done-title")).toHaveText("整理完成", { timeout: 30_000 });
  await expect(page.getByTestId("outcome")).toContainText("2 個重名已加後綴、1 個相同已略過");
  const details = page.getByTestId("plan-tree");
  await details.locator("summary").first().click();
  await expect(details.getByText(/IMG_0001 \(1\)\.jpg/)).toBeVisible();
});

test("[E2E-B-007] 完成畫面：web build 有「試試搜尋」，沒有「在檔案總管開啟」", async ({ page }) => {
  await pickTrip(page);
  await page.getByRole("radio", { name: /依日期/ }).check();
  await page.getByRole("button", { name: "預覽" }).click();
  await page.getByRole("button", { name: "核准並開始" }).click();
  await expect(page.getByTestId("done-title")).toHaveText("整理完成", { timeout: 30_000 });
  await expect(page.getByRole("link", { name: "試試搜尋" })).toBeVisible();
  await expect(page.getByRole("button", { name: "在檔案總管開啟" })).toHaveCount(0);
  // desktop build (mock platform): the button is there and opens the output folder
  await page.addInitScript(() => {
    (window as unknown as { __opened: string[] }).__opened = [];
    (window as unknown as { __OPA_PLATFORM__: unknown }).__OPA_PLATFORM__ = {
      kind: "desktop",
      revealInFileManager: async (a: string, r: string) => void (window as unknown as { __opened: string[] }).__opened.push(`${a}:${r}`),
    };
  });
  await page.reload();
  await page.getByRole("button", { name: "在檔案總管開啟" }).click();
  expect(await page.evaluate(() => (window as unknown as { __opened: string[] }).__opened)).toEqual(["sorted:"]);
});

test("[E2E-B-008] 套用中取消：計畫 partially_applied；「復原」可用且完成後還原", async ({ page, request }) => {
  const t = tree();
  const before = snapshot(t.sorted);
  await request.post("/__e2e/slow", { data: { apply_throttle_s: 0.3 } });
  await pickTrip(page);
  await page.getByRole("radio", { name: /依日期/ }).check();
  await page.getByRole("button", { name: "預覽" }).click();
  await page.getByRole("button", { name: "核准並開始" }).click();
  const progress = page.getByRole("progressbar");
  await expect(progress).toBeVisible();
  await expect.poll(() => Object.values(snapshot(t.sorted)).filter((v) => v !== "dir").length, { timeout: 10_000 }).toBeGreaterThan(0);
  await page.getByRole("button", { name: "取消" }).click();
  await expect(page.getByTestId("plan-status")).toHaveText("部分完成", { timeout: 30_000 });
  const plan = await (await request.get(`/api/v1/output-plans/${page.url().split("/plans/")[1]}`)).json();
  expect(plan.status).toBe("partially_applied");
  await page.getByRole("button", { name: "復原" }).click();
  await expect(page.getByTestId("undone-title")).toHaveText("已復原", { timeout: 30_000 });
  expect(snapshot(t.sorted)).toEqual(before);
});

test("[E2E-B-009] 外接硬碟離線：來源顯示「離線」、等待原因 SOURCE_OFFLINE；改回後自動繼續", async ({ page }) => {
  const t = tree();
  const away = `${t.photos}-unplugged`;
  await pickTrip(page);
  renameSync(t.photos, away); // unplug the drive after choosing the folder
  try {
    await page.getByRole("radio", { name: /依日期/ }).check();
    await page.getByRole("button", { name: "預覽" }).click();
    const waiting = page.getByTestId("job-waiting");
    await expect(waiting).toContainText("來源離線", { timeout: 30_000 });
    await expect(page.getByTestId("source-status")).toHaveText("離線");
    const ops = await (await page.request.get("/api/v1/operations?status=all&type=scan")).json();
    expect(ops[0].waiting_reason).toBe("SOURCE_OFFLINE");
    expect(ops[0].status).toBe("waiting");
  } finally {
    renameSync(away, t.photos); // plug it back in
  }
  await expect(page.getByTestId("plan-summary")).toBeVisible({ timeout: 60_000 });
});

test("[E2E-B-010] 全鍵盤完成旅程 B", async ({ page }) => {
  const t = tree();
  const photosBefore = snapshot(t.photos);
  await page.goto("/organize");
  const press = async (name: string | RegExp, role: "button" | "link" = "button") => {
    const target = page.getByRole(role, { name, exact: typeof name === "string" });
    for (let i = 0; i < 60; i++) {
      if (await target.first().evaluate((el) => el === document.activeElement).catch(() => false)) break;
      await page.keyboard.press("Tab");
    }
    await expect(target.first()).toBeFocused();
    await page.keyboard.press("Enter");
  };
  await press("photos");
  await press("trip");
  await press("選擇此資料夾");
  // method radios: focus the group, choose 依日期 with the arrow keys
  for (let i = 0; i < 40; i++) {
    if (await page.getByRole("radio").first().evaluate((el) => el.closest("fieldset")?.contains(document.activeElement)).catch(() => false)) break;
    await page.keyboard.press("Tab");
  }
  for (let i = 0; i < 4 && !(await page.getByRole("radio", { name: /依日期/ }).isChecked()); i++) await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("radio", { name: /依日期/ })).toBeChecked();
  await press("預覽");
  await expect(page.getByTestId("plan-summary")).toBeVisible({ timeout: 30_000 });
  await press("核准並開始");
  await expect(page.getByTestId("done-title")).toHaveText("整理完成", { timeout: 30_000 });
  await press("復原");
  await expect(page.getByTestId("undone-title")).toHaveText("已復原", { timeout: 30_000 });
  expect(snapshot(t.photos)).toEqual(photosBefore);
});
