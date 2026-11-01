import { defineConfig, devices } from "@playwright/test";

const port = process.env.E2E_PORT || "47811";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["../../tools/tc-reporters/playwright.js"]],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], locale: "zh-TW" } }],
  webServer: {
    command: "node e2e/server.mjs",
    url: `http://127.0.0.1:${port}/api/v1/capabilities`,
    timeout: 120_000,
    reuseExistingServer: false,
  },
});
