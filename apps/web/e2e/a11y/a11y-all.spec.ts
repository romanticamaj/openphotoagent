// Every route the app has gets an axe pass (09b §9.3 A11Y-010). A11Y-001 covers the main routes with content
// (e2e/routes.ts); this spec covers the rest, and fails if a new route appears that nobody checks.
import AxeBuilder from "@axe-core/playwright";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";
import { expect, test } from "../fixtures";
import { ROUTES, go } from "../routes";

test.use({ scenario: { photos: "library", library: true, organize: "by-content-12" } });

// router path → which spec opens it
const MAIN: Record<string, string> = {
  "/": "photos",
  "/welcome": "onboarding",
  "/import": "import",
  "/explore": "explore",
  "/duplicates": "duplicates",
  "/organize": "organize",
  "/plans/$planId": "plan",
  "/review": "review",
  "/models": "models",
  "/models/$modelId": "model card",
  "/system/doctor": "doctor",
  "/search": "search",
  "/settings": "settings",
};
const heading = (p: Page) => p.getByRole("heading", { level: 1 }).first().waitFor();
const EXTRA: Record<string, (p: Page, lang: string) => Promise<void>> = {
  "/map": async (p, l) => void (await go(p, "/map", l), await p.getByTestId("map-borders").waitFor({ state: "attached" })),
  "/people": async (p, l) => void (await go(p, "/people", l), await heading(p)),
  "/people/review": async (p, l) => void (await go(p, "/people/review", l), await p.locator("main").waitFor()),
  // a person's page (FACE-UI-023); this stack has no people, so an unknown id shows the wall
  "/people/$personId": async (p, l) => void (await go(p, "/people/unknown", l), await heading(p)),
  "/settings/integrations": async (p, l) => void (await go(p, "/settings/integrations", l), await heading(p)),
  "/settings/assistant": async (p, l) => void (await go(p, "/settings/assistant", l), await heading(p)),
  "/settings/faces": async (p, l) => void (await go(p, "/settings/faces", l), await p.locator("main").waitFor()),
  "/approvals/$approvalId": async (p, l) => void (await go(p, "/approvals/ap_missing", l), await p.locator("main").waitFor()),
  "/login": async (p, l) => void (await go(p, "/login", l), await p.locator("#login-h").waitFor()),
  "/albums": async (p, l) => void (await go(p, "/albums", l), await heading(p)),
  "/albums/$albumId": async (p, l) => void (await go(p, "/albums/fav-default", l), await heading(p)),
};

test("[A11Y-010] every route of the app has an accessibility check", () => {
  const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../../src/router.tsx"), "utf8");
  const paths = [...src.matchAll(/path: "([^"]+)"/g)].map((m) => m[1]);
  expect(paths.length).toBeGreaterThan(15);
  const names = new Set(ROUTES.map((r) => r.name));
  const unchecked = paths.filter((p) => !(p in EXTRA) && !(p in MAIN && names.has(MAIN[p])));
  expect(unchecked, "routes without an a11y check: " + unchecked.join(", ")).toEqual([]);
});

for (const scheme of ["light", "dark"] as const) {
  for (const lang of ["zh-TW", "en"]) {
    test(`[A11Y-010] axe 其餘路由 0 serious／critical — ${scheme} · ${lang}`, async ({ page }) => {
      test.setTimeout(180_000);
      await page.emulateMedia({ colorScheme: scheme });
      const problems: string[] = [];
      for (const [path, open] of Object.entries(EXTRA)) {
        await open(page, lang);
        await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== "running"));
        const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
        for (const v of res.violations.filter((x) => x.impact === "serious" || x.impact === "critical")) {
          problems.push(`${path}: ${v.id} (${v.impact}) ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
        }
      }
      expect(problems, problems.join("\n")).toEqual([]);
    });
  }
}
