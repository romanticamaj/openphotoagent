// TS mirror of packages/core/src/opagent_core/path_template.py (CORE-TPL-025). Used by the UI for instant previews.
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
export type Template = { source: string };
export function compileTemplate(t: string): Template {
  return { source: t };
}
export function sanitizeSegment(v: string): string {
  return v;
}
export function render(_t: Template, _v: Values, _takenAt: string | null, _mtime: string,
  _fallback: Record<string, string> | null = null, _lang = "zh-TW"): { rel: string; date_source: string | null } {
  return { rel: "", date_source: null };
}
