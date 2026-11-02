import { defineConfig, devices } from "@playwright/test";

const port = process.env.E2E_PORT || "47811";
// docker.yml points the suite at the published container instead of the in-process test stack
const external = process.env.OPAGENT_E2E_BASE_URL;
export default defineConfig({
  testDir: external ? "./e2e/docker" : "./e2e",
  testIgnore: external ? [] : ["docker/**"],
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["../../tools/tc-reporters/playwright.js"]],
  use: { baseURL: external ?? `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], locale: "zh-TW" } }],
  // pinned fonts + Linux Chromium only: visual baselines are compared at 0.1 % (VIS-001/002, ADR-0003)
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.001, animations: "disabled" } },
  webServer: external ? [] : [
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
