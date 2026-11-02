// Playwright reporter: "[ID]" tags in titles -> <repo>/.tc/e2e.json
const { mkdirSync, writeFileSync } = require("node:fs");
const { join, resolve, dirname } = require("node:path");
const ROOT = resolve(__dirname, "..", "..");
const ID = /\[([A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}[a-z]?)\]/g;
class TcReporter {
  constructor() { this.results = {}; }
  onTestEnd(test, result) {
    const title = test.titlePath().join(" > ");
    const outcome = result.status === "passed" ? "passed" : result.status === "skipped" ? "skipped" : "failed";
    for (const m of title.matchAll(ID)) (this.results[m[1]] ||= []).push({ nodeid: title, outcome });
  }
  onEnd() {
    const out = join(ROOT, ".tc", process.env.OPAGENT_E2E_PERF ? "e2e-perf.json" : "e2e.json"); // perf runs separately
    mkdirSync(dirname(out), { recursive: true });
    writeFileSync(out, JSON.stringify(this.results, null, 1));
  }
}
module.exports = TcReporter;
