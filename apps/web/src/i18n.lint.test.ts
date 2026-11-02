import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

describe("i18n", () => {
  it("[I18N-002] no hard-coded user-facing text in JSX (i18next/no-literal-string)", async () => {
    const eslint = new ESLint({ cwd: process.cwd() });
    const results = await eslint.lintFiles(["src/**/*.tsx"]);
    const hits = results.flatMap((r) =>
      r.messages.filter((m) => m.ruleId === "i18next/no-literal-string").map((m) => `${r.filePath}:${m.line} ${m.message}`),
    );
    expect(hits).toEqual([]);
    // the rule must actually be on, or this test proves nothing
    const cfg = await eslint.calculateConfigForFile("src/App.tsx");
    expect(cfg.rules["i18next/no-literal-string"]?.[0]).toBe(2);
  }, 60_000);
});
