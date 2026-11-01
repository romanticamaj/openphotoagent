// Vitest reporter: collects "[ID]" tags from test names into <repo>/.tc/ts-<name>.json (docs/09 §1.2)
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const ID = /\[([A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}[a-z]?)\]/g;

export default class TcReporter {
  constructor(opts = {}) {
    this.name = opts.name || "vitest";
    this.results = {};
  }
  onTestCaseResult(testCase) {
    const state = testCase.result().state;
    const outcome = state === "passed" ? "passed" : state === "failed" ? "failed" : "skipped";
    for (const m of testCase.fullName.matchAll(ID)) {
      (this.results[m[1]] ||= []).push({ nodeid: `${testCase.module.moduleId}::${testCase.fullName}`, outcome });
    }
  }
  onTestRunEnd() {
    const out = join(ROOT, ".tc", `ts-${this.name}.json`);
    mkdirSync(dirname(out), { recursive: true });
    writeFileSync(out, JSON.stringify(this.results, null, 1));
  }
}
