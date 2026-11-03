import { readFileSync, existsSync } from "node:fs";
import { expect, test } from "../fixtures";

// 「連接 AI 助手」 and agent requests (04 §3.10; 05 §3.6). The AI client is played by direct JSON-RPC calls with a PAT.
test.describe("connect AI assistants", () => {
  test.use({ scenario: { photos: "trip", library: true } });

  test("[MCP-PAT-002][MCP-PAT-004] 精靈：預設唯讀、勾縮圖顯示外送說明、先看 diff 再寫、只寫已知路徑並備份", async ({ page, request }) => {
    const cfg = await (await request.get("/__e2e/client-config?client=claude-desktop")).json();
    await page.goto("/settings/integrations");
    await page.getByRole("button", { name: "連接 AI 助手" }).click();
    const wiz = page.getByTestId("mcp-wizard");
    await wiz.getByRole("radio", { name: "Claude Desktop" }).check();
    await expect(wiz.getByTestId("media-egress")).toHaveCount(0);
    await wiz.getByRole("checkbox", { name: /傳送縮圖/ }).check();
    await expect(wiz.getByTestId("media-egress")).toContainText("Claude Desktop");
    await wiz.getByRole("checkbox", { name: /傳送縮圖/ }).uncheck();
    await wiz.getByRole("button", { name: "建立並繼續" }).click();
    await expect(wiz.getByTestId("mcp-credential")).toBeVisible();
    await expect(wiz.getByTestId("mcp-diff")).toContainText('"args":["mcp"]');
    expect(existsSync(cfg.path)).toBe(true);
    const before = readFileSync(cfg.path, "utf8");
    expect(before).not.toContain("openphotoagent"); // nothing written before the person says so
    await wiz.getByRole("button", { name: "寫入設定" }).click();
    await expect(wiz.getByTestId("mcp-written")).toContainText(cfg.path);
    const after = JSON.parse(readFileSync(cfg.path, "utf8"));
    expect(after.mcpServers.openphotoagent.command).toMatch(/^\//); // absolute path (MCP-PAT-003)
    expect(after.mcpServers.other).toBeTruthy(); // existing servers kept
    expect(readFileSync(`${cfg.path}.opagent-backup`, "utf8")).toBe(before);
    expect(JSON.stringify(after)).not.toContain("opagent_pat_"); // the token is in the keyring, never the file
    const tokens = await (await request.get("/api/v1/mcp/tokens")).json();
    expect(tokens.at(-1).scopes).toEqual(["photos:read"]);
  });

  test("[PF-060][PF-061] 測試連線：關閉 MCP → 卡片與「啟用 MCP」修復；撤銷的 token → 「重新產生權杖」開啟精靈", async ({ page, request }) => {
    const tok = await (
      await request.post("/api/v1/mcp/tokens", { data: { name: "Cursor", scopes: ["photos:read"] }, headers: { "X-OPA-UI": "1" } })
    ).json();
    await page.goto("/settings/integrations");
    const sw = page.getByRole("checkbox", { name: "啟用 MCP Server" });
    await expect(sw).toBeChecked();
    const app = page.getByTestId("mcp-app").filter({ hasText: "Cursor" });
    await app.getByRole("button", { name: "測試連線" }).click();
    await expect(app.getByTestId("mcp-test-ok")).toBeVisible();
    await sw.uncheck();
    await app.getByRole("button", { name: "測試連線" }).click();
    const card = app.locator("[data-code]");
    await expect(card).toContainText("MCP 伺服器尚未啟用");
    await card.getByRole("button", { name: "啟用 AI 助手連線" }).click(); // the fix does what it says, then re-tests
    await expect(sw).toBeChecked();
    await expect(app.getByTestId("mcp-test-ok")).toBeVisible();
    // PF-061: a token revoked elsewhere (another window, the CLI) — the row is still on screen
    await request.delete(`/api/v1/mcp/tokens/${tok.id}`, { headers: { "X-OPA-UI": "1" } });
    await app.getByRole("button", { name: "測試連線" }).click();
    await expect(app.locator("[data-code]")).toContainText("MCP token 無效或已過期");
    await app.locator("[data-code]").getByRole("button", { name: "重新產生權杖" }).click();
    await expect(page.getByTestId("mcp-wizard")).toBeVisible();
  });

  test("[MCP-APPR-006][MCP-AUD-004] agent 請求核准卡 → 核准頁顯示提出者與明細；已連線的 App 顯示呼叫與影像數", async ({ page, request }) => {
    const tok = await (
      await request.post("/api/v1/mcp/tokens", { data: { name: "Claude Desktop", scopes: ["photos:read", "organize:propose"] }, headers: { "X-OPA-UI": "1" } })
    ).json();
    await request.post("/__e2e/mcp-apply-grant", { data: { token_id: tok.id } }); // v0.5 grants; never issued by the UI
    await request.put("/api/v1/mcp/settings", { data: { allow_apply_requests: true }, headers: { "X-OPA-UI": "1" } });
    const rpc = async (name: string, args: Record<string, unknown>) =>
      (
        await (
          await request.post("/mcp/v1/rpc", {
            data: { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } },
            headers: { Authorization: `Bearer ${tok.token}`, "MCP-Protocol-Version": "2025-11-25" },
          })
        ).json()
      ).result.structuredContent;
    const plan = await rpc("propose_organize", {
      scope: { filters: {} },
      action: "move",
      target: { source: "sorted" },
      path_template: "{taken_at:%Y}/{stem}{ext}",
    });
    const req = await rpc("apply_organize", { plan_id: plan.plan_id });
    expect(req.status).toBe("approval_pending");
    await page.goto("/");
    await expect(page.getByTestId("approval-card")).toContainText("Claude Desktop");
    await page.getByTestId("approval-card").getByRole("link").click();
    await expect(page.getByTestId("approval-requester")).toContainText(`Claude Desktop（grant #${tok.number}）`);
    await expect(page.getByTestId("approval-actions").locator("tr")).toHaveCount(plan.summary.actions);
    const approve = page.getByRole("button", { name: "核准並套用" });
    await expect(approve).toHaveAttribute("aria-disabled", "true"); // a move needs the second confirmation
    await page.getByRole("checkbox", { name: /移動/ }).check();
    await approve.click();
    await expect(page.getByTestId("approval-status")).toHaveText("已核准", { timeout: 15_000 });
    expect((await rpc("apply_organize", { plan_id: plan.plan_id })).status).toBe("applying");
    await page.goto("/settings/integrations");
    const row = page.getByTestId("mcp-app").filter({ hasText: "Claude Desktop" });
    await expect(row.getByTestId("mcp-app-usage")).toContainText("3 次呼叫");
    await expect(row.getByTestId("mcp-app-usage")).toContainText("0 張影像");
  });
});
