import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

export type Tree = { base: string; photos: string; sorted: string };
export const tree = (): Tree => JSON.parse(readFileSync(join(import.meta.dirname, ".run", "tree.json"), "utf8"));

/** path -> "dir" | sha256:mtimeMs, recursively (backend filesystem snapshot used by E2E assertions). */
export function snapshot(root: string): Record<string, string> {
  const out: Record<string, string> = {};
  const walk = (d: string) => {
    for (const name of readdirSync(d).sort()) {
      const p = join(d, name);
      const st = statSync(p);
      const rel = relative(root, p).split("\\").join("/");
      if (st.isDirectory()) {
        out[rel] = "dir";
        walk(p);
      } else {
        out[rel] = createHash("sha256").update(readFileSync(p)).digest("hex") + ":" + Math.round(st.mtimeMs);
      }
    }
  };
  walk(root);
  return out;
}
