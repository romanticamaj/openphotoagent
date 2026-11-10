// What a planned query becomes (05 §2.3): the planner's filters turn into removable chips, and the chips are the
// filters of the search that follows. A person the query named stays a person filter (SRCH-UI-012): dropping it
// showed the whole library for 「測試A」.
import type { Planner, SearchBody } from "../../lib/api";

export type Chip =
  | { kind: "tag"; value: string }
  | { kind: "year"; value: string }
  | { kind: "range"; value: string; from: string; to: string }
  | { kind: "person"; value: string; id: string };

const PERSON = /^「(.+)」→ 人物/;

export function chipsFrom(p: Planner): Chip[] {
  const out: Chip[] = [];
  const at = p.filters.taken_at;
  if (at) {
    const label = p.explain.find((e) => /→/.test(e) && /\d{4}/.test(e)) ?? `${at.from} – ${at.to}`;
    out.push({ kind: "range", value: label, from: at.from, to: at.to });
  }
  const ids = (p.filters.persons_all as string[] | undefined) ?? [];
  const names = p.explain.map((e) => PERSON.exec(e)?.[1]).filter((x): x is string => Boolean(x));
  ids.forEach((id, i) => out.push({ kind: "person", value: names[i] ?? id, id }));
  return out;
}

/** The query without a person's name: removing the 「人物」 chip takes the name out of the box too (SRCH-UI-013). */
export function withoutPerson(q: string, name: string): string {
  const i = q.toLowerCase().indexOf(name.toLowerCase());
  const s = i < 0 ? q : q.slice(0, i) + " " + q.slice(i + name.length);
  return s.replace(/\s+/g, " ").trim();
}

export function bodyFor(q: string, chips: Chip[], planned: { text: string } | null): SearchBody {
  if (!planned && q) return { q, plan: true, filters: {} };
  const tags = chips.filter((c) => c.kind === "tag").map((c) => c.value);
  const year = chips.find((c) => c.kind === "year")?.value;
  const range = chips.find((c): c is Extract<Chip, { kind: "range" }> => c.kind === "range");
  const people = chips.filter((c): c is Extract<Chip, { kind: "person" }> => c.kind === "person").map((c) => c.id);
  return {
    q: (planned ? planned.text : q) || null,
    filters: {
      ...(tags.length ? { tags_all: tags } : {}),
      ...(range ? { taken_from: range.from, taken_to: range.to } : year ? { taken_from: `${year}-01-01`, taken_to: `${year}-12-31` } : {}),
      ...(people.length ? { persons_all: people } : {}),
    },
  };
}

type T = (k: string, o?: Record<string, unknown>) => string;

/** 「17 張」, or 「最相關的 250 張」 when the image route stopped at its limit and the count is a floor (SRCH-UI-014). */
export function countText(t: T, n: number, capped: boolean, lang: string): string {
  return t(capped ? "search.countCapped" : "search.count", { n: n.toLocaleString(lang) });
}
