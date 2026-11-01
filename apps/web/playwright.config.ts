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
  // pinned fonts + Linux Chromium only: visual baselines are compared at 0.1 % (VIS-001/002, ADR-0003)
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.001, animations: "disabled" } },
  webServer: [
    {
      command: "node e2e/server.mjs",
      url: `http://127.0.0.1:${port}/__e2e/ready`,
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      // the built Storybook (just e2e builds it), served as static files for component snapshots
      command: "node e2e/visual/static.mjs storybook-static 6007",
      url: "http://127.0.0.1:6007/index.json",
      timeout: 30_000,
      reuseExistingServer: false,
    },
  ],
});
