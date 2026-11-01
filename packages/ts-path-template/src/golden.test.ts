import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { TemplateError, compileTemplate, render, sanitizeSegment } from "./index.ts";

type Case = Record<string, any>;
const golden = JSON.parse(readFileSync(join(import.meta.dirname, "../../../fixtures/golden/path_template/cases.json"), "utf8"));

describe("[CORE-TPL-025] TS path_template matches the shared golden", () => {
  it("[CORE-TPL-025] has at least 200 cases", () => expect(golden.cases.length).toBeGreaterThanOrEqual(200));
  it("[CORE-TPL-025] every case matches Python", () => {
    for (const c of golden.cases as Case[]) {
      if (c.kind === "compile_error") {
        let err: unknown = null;
        try { compileTemplate(c.template); } catch (e) { err = e; }
        expect(err, c.template).toBeInstanceOf(TemplateError);
        expect([(err as TemplateError).code, (err as TemplateError).pos], c.template).toEqual([c.code, c.pos]);
      } else if (c.kind === "segment") {
        expect(sanitizeSegment(c.value), JSON.stringify(c.value)).toBe(c.out);
      } else {
        const r = render(compileTemplate(c.template), c.values, c.taken_at, c.mtime, c.fallback, c.lang);
        expect([r.rel, r.date_source], c.template).toEqual([c.rel, c.date_source]);
      }
    }
  });
});
