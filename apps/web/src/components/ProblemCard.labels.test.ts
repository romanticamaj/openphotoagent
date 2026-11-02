import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import i18n from "../lib/i18n";
import { fixLabel, type Problem } from "./ProblemCard";

// every error code with its catalog fixes, as generated for VIS-003
const problems: Problem[] = [...readFileSync(join(__dirname, "ProblemCard.generated.stories.tsx"), "utf8").matchAll(/problem: (\{.*\}) \} \};$/gm)].map((m) => JSON.parse(m[1]));

describe("fix button labels", () => {
  it("[I18N-007] every fix of every error code shows translated text in zh-TW and en", async () => {
    expect(problems.length).toBeGreaterThan(60);
    const raw: string[] = [];
    for (const lng of ["zh-TW", "en"]) {
      await i18n.changeLanguage(lng);
      for (const p of problems) {
        (p.fixes ?? []).forEach((f, i) => {
          const label = fixLabel(p, f, i, i18n.t.bind(i18n), (k) => i18n.exists(k));
          if (!label || /^fix\./.test(label)) raw.push(`${lng} ${p.code}[${i}] ${f.id}: ${label}`);
        });
      }
    }
    expect(raw, raw.slice(0, 30).join("\n")).toEqual([]);
  });
});
