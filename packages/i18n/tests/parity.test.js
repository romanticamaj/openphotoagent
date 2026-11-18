import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (f) => JSON.parse(readFileSync(join(root, f), "utf8"));

function flatten(obj, prefix = "") {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === "object" ? flatten(v, `${prefix}${k}.`) : [[`${prefix}${k}`, v]],
  );
}

// strings that are the same in both languages on purpose (product names, units, formats, symbols)
const SAME_OK = new Set(["OpenPhotoAgent", "MCP", "PAT", "GPU", "VRAM", "OCR", "EXIF", "Immich", "Label Studio", "Ollama"]);
const isSameOk = (v) =>
  SAME_OK.has(v) || /^[\d\s.,:%/×+\-–—()#{}·…]*$/.test(v) || /^\{\{?[a-z_]+\}?\}$/i.test(v) || v.length <= 3;
// keys whose text is the same in both languages because it is a product name or a unit pattern, reviewed one by one
const SAME_KEYS = new Set([
  "common.percent", // "{n}%"
  "onboarding.plan.cuda", // CUDA
  "onboarding.plan.metal", // Metal
  "models.vram", // "VRAM {gb} GB"
  "models.ram", // "RAM {gb} GB"
  "mcp.client.claude-desktop",
  "mcp.client.claude-code",
  "mcp.client.cursor",
  "ask.elapsed", // "{s} s"
  "gpu.metal.title", // Apple Metal
]);
const vars = (s) => [...String(s).matchAll(/\{\{?\s*([a-zA-Z_][\w.]*)[^}]*\}?\}/g)].map((m) => m[1]).sort();

const SETS = [
  ["locales/en.json", "locales/zh-TW.json"],
  ["errors.en.json", "errors.zh-TW.json"],
  ["errors.solo.en.json", "errors.solo.zh-TW.json"],
  ["checks.en.json", "checks.zh-TW.json"],
];

describe("[I18N-010] both languages complete", () => {
  for (const [enF, zhF] of SETS) {
    const en = Object.fromEntries(flatten(read(enF)));
    const zh = Object.fromEntries(flatten(read(zhF)));
    it(`[I18N-010] ${enF} ↔ ${zhF}: same keys, nothing empty`, () => {
      expect(Object.keys(zh).sort()).toEqual(Object.keys(en).sort());
      for (const [k, v] of [...Object.entries(en), ...Object.entries(zh)]) expect(String(v).trim(), k).not.toBe("");
    });
    it(`[I18N-010] ${zhF}: nothing left untranslated`, () => {
      const same = Object.keys(en).filter(
        (k) => typeof en[k] === "string" && en[k] === zh[k] && !isSameOk(en[k]) && !SAME_KEYS.has(k),
      );
      expect(same).toEqual([]);
    });
    it(`[I18N-010] ${enF} ↔ ${zhF}: the same interpolation variables`, () => {
      const diff = Object.keys(en).filter((k) => JSON.stringify(vars(en[k])) !== JSON.stringify(vars(zh[k])));
      expect(diff.map((k) => `${k}: ${vars(en[k])} vs ${vars(zh[k])}`)).toEqual([]);
    });
  }
});
