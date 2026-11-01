import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "locales");

function flatten(obj, prefix = "") {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === "object" ? flatten(v, `${prefix}${k}.`) : [[`${prefix}${k}`, v]],
  );
}

const load = (lang) => Object.fromEntries(flatten(JSON.parse(readFileSync(join(dir, `${lang}.json`), "utf8"))));

describe("[I18N-001] no missing keys", () => {
  const files = readdirSync(dir).filter((f) => f.endsWith(".json")).map((f) => f.replace(/\.json$/, ""));
  it("[I18N-001] zh-TW and en exist", () => {
    expect(files).toEqual(expect.arrayContaining(["zh-TW", "en"]));
  });
  it("[I18N-001] zh-TW and en have identical key sets", () => {
    expect(Object.keys(load("en")).sort()).toEqual(Object.keys(load("zh-TW")).sort());
  });
  it("[I18N-001] no empty strings", () => {
    for (const lang of ["zh-TW", "en"]) {
      for (const [k, v] of Object.entries(load(lang))) expect(v, `${lang}:${k}`).not.toBe("");
    }
  });
});
