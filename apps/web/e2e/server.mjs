// Starts the E2E stack (opagent_testing.e2e_stack): embedded PG + API serving dist/ + in-process Worker on
// FakeBackend + recorded HF server + synthesized hardware profile. Specs reset it per test via POST /__e2e/reset.
import { spawn } from "node:child_process";
import { chmodSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..", "..", "..");
const base = mkdtempSync(join(tmpdir(), "opagent-e2e-"));
chmodSync(base, 0o755); // embedded PG may run as a separate OS user (containers running as root)
const port = process.env.E2E_PORT || "47811";
const child = spawn(
  "uv",
  ["run", "--project", repo, "python", "-m", "opagent_testing.e2e_stack", "--port", port, "--base", base,
   "--web-dist", join(here, "..", "dist")],
  { stdio: "inherit", env: { ...process.env, UV_SYSTEM_CERTS: "1" } },
);
const stop = () => child.kill("SIGTERM");
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
// the stack holds a whole PG cluster plus photo trees: never leave it behind in the temp dir
child.on("exit", (code) => {
  rmSync(base, { recursive: true, force: true });
  process.exit(code ?? 0);
});
