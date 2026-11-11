// What a planned query becomes (05 §2.3): the planner's filters turn into removable chips, and the chips are the
// filters of the search that follows. A person the query named stays a person filter (SRCH-UI-012): dropping it
// showed the whole library for 「測試A」.
import type { Planner, SearchBody } from "../../lib/api";

export type Chip =
  | { kind: "tag"; value: string }
  | { kind: "year"; value: string }
  | { kind: "range"; value: string; from: string; to: string }
  | { kind: "person"; value: string; id: string }
  /** 「只看開心」: the same query, only the photos that show the expression (SRCH-CNC-012) */
  | { kind: "soft"; value: string; word: string };

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
    ...(chips.some((c) => c.kind === "soft") ? { soft_only: true } : {}),
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

/** Found by what the photo shows, not by a word or tag it carries: the only case 「以文搜圖」 explains (SRCH-UI-015). */
export function onlyByMeaning(matched: string[]): boolean {
  return matched.includes("image_vector") && !matched.some((m) => m === "fulltext" || m === "tag" || m === "bigram");
}

export type Soft = { word: string; key: string; shown: number };

/** An expression ranks the results without filtering them (ADR-0032 addendum, SRCH-CNC-010): the photos that show it
 * come first, a divider says where they end, and one button turns it into a filter. */
export function softView(
  t: T,
  soft: Soft[] | undefined,
  total: number,
  chips: Chip[],
): { at: number | null; divider: string | null; note: string | null; only: string | null; chip: Chip } | null {
  const s = soft?.[0];
  if (!s || s.shown >= total || chips.some((c) => c.kind === "soft")) return null;
  const chip: Chip = { kind: "soft", value: s.key, word: s.word };
  if (!s.shown) return { at: null, divider: null, note: t("search.softNone", { word: s.word }), only: null, chip };
  return {
    at: s.shown,
    divider: t("search.softDivider", { n: (total - s.shown).toLocaleString(), word: s.word }),
    note: null,
    only: t("search.softOnly", { word: s.word, n: s.shown.toLocaleString() }),
    chip,
  };
}

/** At the end of a cut list: the rest exist but are less alike (SRCH-UI-016). None while more pages are coming. */
export function cappedNote(t: T, n: number, capped: boolean, more: boolean): string | null {
  return capped && !more ? t("search.cappedNote", { n: n.toLocaleString() }) : null;
}

/** What a chip says, and what its remove button names (SRCH-UI-017): never an internal key. */
export function chipLabel(t: T, c: Chip, tagNames: Record<string, string>): string {
  if (c.kind === "person") return t("search.personChip", { name: c.value });
  if (c.kind === "soft") return t("search.softChip", { word: c.word });
  return (c.kind === "tag" && tagNames[c.value]) || c.value;
}

/** Weak look-alikes come last and stay folded until asked for (ADR-0040, SRCH-SEM-007). Only once the photos
 * before them are loaded (SRCH-SEM-008): the fold sat after page one, over strong photos still to come. `fetch`: they
 * are the next page, so the button loads it. */
export function weakTail(
  t: T,
  from: number | null | undefined,
  n: number,
  shown: boolean,
  loaded: number,
): { from: number; more: string; fetch: boolean } | null {
  if (shown || !n || !from || loaded < from) return null;
  return { from, more: t("search.weakMore", { n: n.toLocaleString() }), fetch: loaded === from };
}

/** Once asked, the weak look-alikes all show: keep loading pages until they are in (SRCH-SEM-009). */
export function weakNeedsMore(shown: boolean, loaded: number, total: number, hasNext: boolean, fetching: boolean): boolean {
  return shown && hasNext && !fetching && loaded < total;
}

