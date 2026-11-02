// Starts the E2E stack (opagent_testing.e2e_stack): embedded PG + API serving dist/ + in-process Worker on
// FakeBackend + recorded HF server + synthesized hardware profile. Specs reset it per test via POST /__e2e/reset.
import { spawn } from "node:child_process";
import { chmodSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..", "..", "..");
const port = process.env.E2E_PORT || "47811";
// One fixed directory per port, reclaimed by the stack at start-up (it stops a leftover postmaster, then wipes
// it): Playwright SIGKILLs its web servers, so no exit handler can be relied on to clean up.
const base = join(tmpdir(), `opagent-e2e-${port}`);
mkdirSync(base, { recursive: true });
chmodSync(base, 0o755); // embedded PG may run as a separate OS user (containers running as root)
const child = spawn(
  "uv",
  ["run", "--project", repo, "python", "-m", "opagent_testing.e2e_stack", "--port", port, "--base", base,
   "--web-dist", join(here, "..", "dist")],
  { stdio: "inherit", env: { ...process.env, UV_SYSTEM_CERTS: "1" } },
);
const stop = () => child.kill("SIGTERM");
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
child.on("exit", (code) => process.exit(code ?? 0));
