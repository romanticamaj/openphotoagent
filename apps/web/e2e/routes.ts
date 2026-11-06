// Main routes with real content, shared by the accessibility (A11Y-001, I18N-004/005) and page snapshot (VIS-002)
// specs. Navigation uses roles/test ids that do not depend on the UI language (pseudo-locale included).
import type { Page } from "@playwright/test";
import type { Stack } from "./fixtures";

export type Route = { name: string; open: (page: Page, stack: Stack, lang: string) => Promise<void> };

export const go = (page: Page, path: string, lang: string) => page.goto(`${path}${path.includes("?") ? "&" : "?"}lang=${lang}`);

export const ROUTES: Route[] = [
  { name: "onboarding", open: (p, _s, l) => go(p, "/welcome", l).then(() => undefined) },
  { name: "duplicates", open: async (p, _s, l) => void (await go(p, "/duplicates", l), await p.getByRole("heading", { level: 1 }).waitFor()) },
  { name: "import", open: async (p, _s, l) => void (await go(p, "/import", l), await p.getByRole("heading", { level: 1 }).waitFor()) },
  { name: "explore", open: async (p, _s, l) => void (await go(p, "/explore", l), await p.getByRole("heading", { level: 1 }).waitFor()) },
  { name: "photos", open: async (p, _s, l) => void (await go(p, "/", l), await p.getByRole("gridcell").first().waitFor()) },
  {
    name: "lightbox",
    open: async (p, _s, l) => {
      await go(p, "/", l);
      await p.getByRole("gridcell").first().click();
      await p.getByRole("dialog").first().waitFor();
      await p.keyboard.press("i");
      await p.getByTestId("info-panel").waitFor();
    },
  },
  {
    name: "organize",
    open: async (p, _s, l) => {
      await go(p, "/organize", l);
      await p.getByRole("button", { name: "photos" }).click();
      await p.getByRole("button", { name: "trip", exact: true }).click();
      await p.getByTestId("use-folder").click();
      await p.getByRole("radio").nth(1).check();
    },
  },
  {
    name: "plan",
    open: async (p, s, l) => {
      const j = await (await p.request.get(`/api/v1/jobs/${s.job_id}`)).json();
      await go(p, `/plans/${j.plan_id}`, l);
      await p.getByTestId("plan-summary").waitFor();
    },
  },
  { name: "review", open: async (p, s, l) => void (await go(p, `/review?job=${s.job_id}`, l), await p.getByRole("heading").first().waitFor()) },
  { name: "models", open: async (p, _s, l) => void (await go(p, "/models", l), await p.getByRole("table").waitFor()) },
  { name: "model card", open: async (p, _s, l) => void (await go(p, "/models/qwen3-vl-8b-instruct", l), await p.getByTestId("license").waitFor()) },
  { name: "doctor", open: async (p, _s, l) => void (await go(p, "/system/doctor", l), await p.getByTestId("doctor-summary").waitFor()) },
  { name: "search", open: async (p, _s, l) => void (await go(p, "/search?q=photo", l), await p.getByTestId("search-result").first().waitFor()) },
  {
    name: "activity",
    open: async (p, _s, l) => {
      await go(p, "/models", l);
      // the button, not Ctrl+J: right after navigation the shortcut listener may not be attached yet (see OP-012)
      await p.getByTestId("activity-button").click();
      await p.locator(".activity-center").waitFor();
    },
  },
  { name: "settings", open: async (p, _s, l) => void (await go(p, "/settings", l), await p.getByRole("combobox").first().waitFor()) },
];

