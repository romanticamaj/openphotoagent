import type { Locator } from "@playwright/test";
import { expect, test } from "../fixtures";

const VLM = "qwen3-vl-8b-instruct@q4_k_m";

async function badge(l: Locator) {
  const b = l.getByTestId("compat-badge").first();
  await expect(b).toBeVisible();
  return { text: (await b.textContent())!.trim(), level: await b.getAttribute("data-level"), reason: await b.getAttribute("data-reason") };
}

test.describe("models", () => {
  // reference machine: the recommended VLM is Qwen3-VL 8B q4_k_m and no driver problem blocks first
  test.use({ scenario: { hw: "rtx5070ti-win11", photos: "trip", models: "none" } });

  test("[MDL-005] 四處一致：選擇器、模型卡、精靈、preflight 的徽章文字與 reason code 相同", async ({ page }) => {
    await page.goto("/models");
    const row = page.getByRole("row", { name: /Qwen3-VL 8B/ });
    const inList = await badge(row);
    await row.getByRole("link").first().click();
    const card = page.getByTestId("variant-q4_k_m");
    const inCard = await badge(card);
    await page.goto("/welcome");
    await page.getByRole("button", { name: "開始" }).click();
    const inWizard = await badge(page.getByTestId("plan-card"));
    await page.goto("/organize");
    await page.getByRole("button", { name: "photos" }).click();
    await page.getByRole("button", { name: "trip", exact: true }).click();
    await page.getByRole("button", { name: "選擇此資料夾" }).click();
    await page.getByRole("radio", { name: /依內容/ }).check();
    const inPreflight = await badge(page.getByTestId("readiness").getByTestId(`model-${VLM}`));
    for (const other of [inCard, inWizard, inPreflight]) expect(other).toEqual(inList);
    // never colour alone: icon + text (A11Y-006)
    expect(inList.text).toMatch(/^[●◐○?] \S+/);
  });

  test("[MDL-006] 模型卡：未下載的 variant 顯示大小、VRAM／RAM、授權、能力；速度欄標「估算」", async ({ page }) => {
    await page.goto("/models/qwen3-vl-8b-instruct");
    const v = page.getByTestId("variant-q4_k_m");
    await expect(v.getByTestId("size")).toHaveText(/\d.*(MB|GB)/);
    await expect(v.getByTestId("vram")).toHaveText(/VRAM [\d.]+ GB/);
    await expect(v.getByTestId("ram")).toHaveText(/RAM [\d.]+ GB/);
    await expect(page.getByTestId("license")).toHaveText(/Apache-2\.0/);
    await expect(page.getByTestId("capabilities")).toContainText("caption");
    await expect(v.getByTestId("speed")).toContainText("估算");
    await expect(v.getByTestId("installed")).toHaveText("未下載");
  });

  test("[PF-077] 自動解鎖：MODEL_DOWNLOADING → model.installed 後重算、按鈕解鎖、aria-live 播報；勾「就緒後自動開始」自動送出", async ({ page, request }) => {
    const roots = await (await request.get("/api/v1/roots")).json();
    await request.post("/api/v1/onboarding/install", { data: { worker_id: roots[0].worker_id, variants: ["siglip2-so400m-384@onnx-int8", VLM] } });
    await page.goto("/organize");
    await page.getByRole("button", { name: "photos" }).click();
    await page.getByRole("button", { name: "trip", exact: true }).click();
    await page.getByRole("button", { name: "選擇此資料夾" }).click();
    await page.getByRole("radio", { name: /依內容/ }).check();
    const create = page.getByRole("button", { name: "建立整理計畫" });
    await expect(create).toBeDisabled();
    await expect(create).toHaveAccessibleDescription(/下載中/);
    await page.getByRole("checkbox", { name: "就緒後自動開始" }).check();
    const live = page.getByTestId("readiness-live");
    await expect(live).toHaveAttribute("aria-live", "polite");
    await expect(live).toHaveText("已就緒，可以開始", { timeout: 60_000 });
    // auto-submitted: the job runs without another click
    await expect(page.getByTestId("review-banner").or(page.getByTestId("plan-summary"))).toBeVisible({ timeout: 60_000 });
  });
});

test.describe("activity", () => {
  test.use({ scenario: { hw: "rtx5070ti-win11", photos: "trip", models: "none" } });

  test("[OP-006][ACT-004] 重啟恢復：下載與 Job 進行中時重啟 → 恢復卡片；下載自動繼續；Job 顯示「繼續」", async ({ page, request }) => {
    await request.post("/__e2e/slow-downloads", { data: { rate_bps: 200_000 } });
    const roots = await (await request.get("/api/v1/roots")).json();
    await request.post("/api/v1/onboarding/install", { data: { worker_id: roots[0].worker_id, variants: [VLM] } });
    const src = (await (await request.post("/api/v1/sources", { data: { root_alias: "photos", sub_path: "trip" } })).json()).id;
    await request.post("/__e2e/attach-models");
    await request.post("/__e2e/slow", { data: { vlm_latency_s: 0.5 } });
    const job = await (await request.post("/api/v1/jobs", { data: { kind: "organize", preset: "by-content-12", source_id: src, target: { root_alias: "sorted", sub_path: "" } } })).json();
    await expect.poll(async () => (await (await request.get(`/api/v1/jobs/${job.id}`)).json()).counters?.done ?? 0, { timeout: 30_000 }).toBeGreaterThan(0);
    await request.post("/__e2e/restart");
    await page.goto("/");
    const card = page.getByTestId("recovery-card");
    await expect(card).toBeVisible({ timeout: 30_000 });
    await expect(card).toContainText("下載");
    await expect(card).toContainText("整理");
    await expect(card.getByRole("button", { name: "全部繼續" })).toBeVisible();
    await expect(card.getByRole("button", { name: "逐一檢視" })).toBeVisible();
    // the download resumed by itself (auto_resume); the job waits for the person
    await page.keyboard.press("ControlOrMeta+j");
    const drawer = page.getByRole("dialog", { name: "活動" });
    await expect(drawer.getByText("已自動繼續")).toBeVisible({ timeout: 30_000 });
    await expect(drawer.getByRole("button", { name: "繼續" })).toBeVisible();
  });

  test("[OP-012] 取消模式：graceful 等目前批次；immediate 中止並歸還 lease，丟棄 > 1 分鐘時需確認", async ({ page, request }) => {
    await request.post("/__e2e/attach-models");
    await request.post("/__e2e/slow", { data: { vlm_latency_s: 6 } }); // a batch of 16 ≈ 96 s of work
    const src = (await (await request.post("/api/v1/sources", { data: { root_alias: "photos", sub_path: "trip" } })).json()).id;
    const job = await (await request.post("/api/v1/jobs", { data: { kind: "organize", preset: "by-content-12", source_id: src, target: { root_alias: "sorted", sub_path: "" } } })).json();
    await expect.poll(async () => (await (await request.get(`/api/v1/jobs/${job.id}`)).json()).counters?.leased ?? 0, { timeout: 30_000 }).toBeGreaterThan(0);
    await page.goto("/");
    // the button, not Ctrl+J: right after navigation the shortcut listener may not be attached yet
    await page.getByRole("button", { name: /^活動/ }).click();
    const drawer = page.getByRole("dialog", { name: "活動" });
    const row = drawer.locator(`[data-op="${job.id}"]`);
    await row.getByRole("button", { name: "取消" }).click();
    const menu = page.getByRole("dialog", { name: "取消這個工作？" });
    await expect(menu).toContainText("等目前批次完成");
    await menu.getByRole("button", { name: "立即中止" }).click();
    await expect(menu).toContainText(/會丟掉目前這批 \d+ 張的結果（約 .+ 工作量）/);
    await menu.getByRole("button", { name: "確定中止" }).click();
    await expect.poll(async () => (await (await request.get(`/api/v1/jobs/${job.id}`)).json()).status, { timeout: 10_000 }).toBe("cancelled");
    const j = await (await request.get(`/api/v1/jobs/${job.id}`)).json();
    expect(j.counters.leased ?? 0).toBe(0); // leases given back
  });
});
