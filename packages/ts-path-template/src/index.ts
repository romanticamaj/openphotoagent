// TS mirror of packages/core/src/opagent_core/path_template.py (CORE-TPL-025). Used by the UI for instant previews.
// Any behaviour change must land in both implementations and in fixtures/golden/path_template/cases.json.

export class TemplateError extends Error {
  constructor(public code: string, public pos: number) {
    super(`${code} at ${pos}`);
  }
}

export type Values = {
  stem: string; ext: string; category?: string | null; category_key?: string | null; country?: string | null;
  admin1?: string | null; city?: string | null; source?: string | null; tags?: [string, number][];
  tag_display?: Record<string, string>;
};
type Part = { literal?: string; variable?: string; fmt?: string | null };
export type Template = { source: string; parts: Part[]; usesDate: boolean };

const TOKEN = /\{([a-z_]+)(?::([^}]*))?\}/g;
const DATE_ALIASES: Record<string, string> = { YYYY: "%Y", "YYYY-MM": "%Y-%m", MM: "%m", DD: "%d", "YYYY-MM-DD": "%Y-%m-%d" };
const KNOWN = new Set(["category", "category_key", "taken_at", "date", "stem", "ext", "country", "admin1", "city", "tag", "source"]);
const STRF_OK = new Set("YmdHMSyj%".split(""));
const MAX_SEGMENT = 255;
const MAX_TEMPLATE = 200;
const REPLACE = new Set([..."/\\:*?\"<>|", "／", "∕", "⁄", "＼"]);
const REMOVE = new Set<string>(["\x7f", "​", "‎", "‏", "﻿"]);
for (let c = 0; c < 0x20; c++) REMOVE.add(String.fromCharCode(c));
for (let c = 0x202a; c < 0x202f; c++) REMOVE.add(String.fromCharCode(c));
for (let c = 0x2066; c < 0x206a; c++) REMOVE.add(String.fromCharCode(c));
const RESERVED = new Set(["CON", "PRN", "AUX", "NUL", ...[1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap((i) => [`COM${i}`, `LPT${i}`])]);
const UNKNOWN: Record<string, string> = { "zh-TW": "_未知", en: "_unknown" };
const MARK = /^\p{M}$/u;
const enc = new TextEncoder();
const bytes = (s: string) => enc.encode(s).length;

// Python str.isspace() equivalent (used by strip())
const PY_SPACE = new Set([..." \t\n\r\x0b\x0c\x1c\x1d\x1e\x1f\x85\xa0     　"]);
for (let c = 0x2000; c <= 0x200a; c++) PY_SPACE.add(String.fromCharCode(c));
const isSpace = (ch: string) => PY_SPACE.has(ch);
function pyStrip(s: string, chars?: (ch: string) => boolean): string {
  const test = chars ?? isSpace;
  const a = Array.from(s);
  let i = 0, j = a.length;
  while (i < j && test(a[i])) i++;
  while (j > i && test(a[j - 1])) j--;
  return a.slice(i, j).join("");
}
function pyRstrip(s: string, chars: (ch: string) => boolean): string {
  const a = Array.from(s);
  let j = a.length;
  while (j > 0 && chars(a[j - 1])) j--;
  return a.slice(0, j).join("");
}

function graphemes(s: string): string[] {
  const out: string[] = [];
  let joinNext = false;
  for (const ch of s) {
    const cp = ch.codePointAt(0)!;
    const extend = MARK.test(ch) || (cp >= 0xfe00 && cp <= 0xfe0f) || (cp >= 0x1f3fb && cp <= 0x1f3ff) ||
      (cp >= 0xe0020 && cp <= 0xe007f) || ch === "‍";
    if (out.length && (extend || joinNext)) out[out.length - 1] += ch;
    else out.push(ch);
    joinNext = ch === "‍";
  }
  return out;
}

export function truncateBytes(s: string, limit: number): string {
  let used = 0;
  const out: string[] = [];
  for (const g of graphemes(s)) {
    const n = bytes(g);
    if (used + n > limit) break;
    out.push(g);
    used += n;
  }
  return out.join("");
}

function fixReserved(s: string): string {
  const i = s.indexOf(".");
  const base = i < 0 ? s : s.slice(0, i);
  return RESERVED.has(base.toUpperCase()) ? `${base}_${i < 0 ? "" : s.slice(i)}` : s;
}

export function sanitizeSegment(value: string, limit = MAX_SEGMENT): string {
  let s = value.normalize("NFC");
  s = Array.from(s).filter((ch) => !REMOVE.has(ch)).map((ch) => (REPLACE.has(ch) ? "_" : ch)).join("");
  s = pyStrip(pyRstrip(pyStrip(s), (ch) => ch === "." || ch === " "));
  if (!s || [...s].every((ch) => ch === ".")) return "_";
  s = fixReserved(s);
  return truncateBytes(s, limit) || "_";
}

function checkStrftime(fmt: string, pos: number) {
  for (let i = 0; i < fmt.length; i++) {
    if (fmt[i] === "%") {
      if (i + 1 >= fmt.length || !STRF_OK.has(fmt[i + 1])) throw new TemplateError("STRFTIME_NOT_ALLOWED", pos);
      i++;
    }
  }
}

export function compileTemplate(template: string): Template {
  if (Array.from(template).length > MAX_TEMPLATE) throw new TemplateError("TEMPLATE_TOO_LONG", MAX_TEMPLATE + 1);
  const skeleton = template.replace(TOKEN, "x");
  if (skeleton.includes("\\") || skeleton.startsWith("/") || /^[A-Za-z]:/.test(skeleton)) throw new TemplateError("TEMPLATE_PATH_ESCAPE", 1);
  if (skeleton.split("/").some((s) => s === "" || s === "." || s === "..")) throw new TemplateError("TEMPLATE_PATH_ESCAPE", 1);
  const parts: Part[] = [];
  let pos = 0;
  for (const m of template.matchAll(TOKEN)) {
    const start = m.index!;
    if (start > pos) parts.push({ literal: template.slice(pos, start) });
    let variable = m[1];
    let fmt: string | null = m[2] ?? null;
    const p = Array.from(template.slice(0, start)).length + 1;
    if (!KNOWN.has(variable)) throw new TemplateError("UNKNOWN_VAR", p);
    if (variable === "date") {
      variable = "taken_at";
      fmt = DATE_ALIASES[fmt || "YYYY"] ?? fmt;
    }
    if (variable === "taken_at" && fmt) checkStrftime(fmt, p);
    if (variable === "tag" && !fmt) throw new TemplateError("TAG_PREFIX_REQUIRED", p);
    parts.push({ variable, fmt });
    pos = start + m[0].length;
  }
  if (pos < template.length) parts.push({ literal: template.slice(pos) });
  return { source: template, parts, usesDate: parts.some((x) => x.variable === "taken_at") };
}

/** Formats an ISO-8601 timestamp *in its own offset* (capture-local time, CORE-TPL-003). */
function strftime(iso: string, fmt: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/.exec(iso)!;
  const [Y, mo, d, H, M, S] = m.slice(1).map(Number);
  const doy = Math.round((Date.UTC(Y, mo - 1, d) - Date.UTC(Y, 0, 1)) / 86400000) + 1;
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  const map: Record<string, string> = {
    Y: pad(Y, 4), m: pad(mo), d: pad(d), H: pad(H), M: pad(M), S: pad(S), y: pad(Y % 100), j: pad(doy, 3), "%": "%",
  };
  return fmt.replace(/%(.)/g, (_, c: string) => map[c]);
}

function tagValue(v: Values, prefix: string): string | null {
  let best: [number, string] | null = null;
  for (const [key, score] of v.tags ?? []) {
    if (!key.startsWith(prefix + ".")) continue;
    if (!best || score > best[0] || (score === best[0] && key > best[1])) best = [score, key];
  }
  if (!best) return null;
  return v.tag_display?.[best[1]] || best[1].split(".").pop()!;
}

export function render(tpl: Template, v: Values, takenAt: string | null, mtime: string,
  fallback: Record<string, string> | null = null, lang = "zh-TW"): { rel: string; date_source: string | null } {
  const when = takenAt ?? mtime;
  const source = takenAt ? "exif_offset" : "mtime";
  const out: string[] = [];
  for (const p of tpl.parts) {
    if (p.literal !== undefined) out.push(p.literal);
    else if (p.variable === "taken_at") out.push(strftime(when, p.fmt || "%Y-%m-%d"));
    else if (p.variable === "ext") out.push(v.ext);
    else {
      let raw: string | null | undefined = p.variable === "tag" ? tagValue(v, p.fmt ?? "") : (v as Record<string, any>)[p.variable!];
      if (raw === null || raw === undefined || raw === "") raw = fallback?.[p.variable!] || UNKNOWN[lang] || UNKNOWN.en;
      out.push(sanitizeSegment(String(raw)));
    }
  }
  const segs = out.join("").split("/");
  const last = segs[segs.length - 1];
  if (bytes(last) > MAX_SEGMENT) {
    const ext = v.ext && last.endsWith(v.ext) ? v.ext : "";
    segs[segs.length - 1] = truncateBytes(last.slice(0, last.length - ext.length), MAX_SEGMENT - bytes(ext)) + ext;
  }
  return { rel: segs.map((s) => truncateBytes(s, MAX_SEGMENT)).join("/"), date_source: tpl.usesDate ? source : null };
}
