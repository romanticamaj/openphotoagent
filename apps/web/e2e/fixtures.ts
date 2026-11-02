// Shared Playwright fixtures: every test starts from a fresh stack scenario (POST /__e2e/reset).
import { test as base, expect } from "@playwright/test";

export type Scenario = {
  hw?: string;
  photos?: "trip" | "library" | "empty";
  net?: "on" | "off";
  library?: boolean;
  organize?: string | null;
  vlm_p?: number;
  models?: "ready" | "none";
};
export type Stack = { worker_id: string; base: string; photos: string; sorted: string; source_id?: string; job_id?: string };

export const test = base.extend<{ scenario: Scenario; stack: Stack }>({
  scenario: [{}, { option: true }],
  stack: [
    async ({ request, scenario }, use) => {
      const r = await request.post("/__e2e/reset", { data: scenario, timeout: 180_000 });
      expect(r.ok(), await r.text()).toBeTruthy();
      await use((await r.json()) as Stack);
    },
    { auto: true, timeout: 180_000 },
  ],
});
export { expect };
