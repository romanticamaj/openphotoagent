// Starts the all-in-one backend (embedded PG + api serving dist/ + worker) on a fresh photo tree for E2E.
// The tree location is written to e2e/.run/tree.json so specs can verify the filesystem directly.
import { spawn } from "node:child_process";
import { chmodSync, cpSync, mkdirSync, mkdtempSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..", "..", "..");
const base = mkdtempSync(join(tmpdir(), "opagent-e2e-"));
chmodSync(base, 0o755); // embedded PG may run as a separate OS user (containers running as root)
const photos = join(base, "roots", "photos");
const sorted = join(base, "roots", "sorted");
mkdirSync(join(photos, "trip"), { recursive: true });
mkdirSync(join(photos, "other"), { recursive: true });
mkdirSync(sorted, { recursive: true });
for (const f of readdirSync(join(repo, "fixtures/photos/basic"))) {
  if (f.endsWith(".jpg")) cpSync(join(repo, "fixtures/photos/basic", f), join(photos, "trip", f), { preserveTimestamps: true });
}
mkdirSync(join(here, ".run"), { recursive: true });
writeFileSync(join(here, ".run", "tree.json"), JSON.stringify({ base, photos, sorted }));
const port = process.env.E2E_PORT || "47811";
const child = spawn(
  "uv",
  ["run", "--project", repo, "opagent", "dev", "--port", port, "--roots", `photos=${photos}:ro,sorted=${sorted}:rw`,
   "--web-dist", join(here, "..", "dist")],
  { stdio: "inherit", env: { ...process.env, OPAGENT_HOME: join(base, "home"), UV_SYSTEM_CERTS: "1" } },
);
const stop = () => child.kill("SIGTERM");
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
child.on("exit", (code) => process.exit(code ?? 0));
