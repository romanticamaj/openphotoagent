// Memory titles from templates, no model (ADR-0049 addendum 8, MEM-055): 「今年的足球」, 「去年的足球」, this year's
// outings without the year, and how many days an outing ran. `today` is the library's, from the API (MEM-054).
type T = (k: string, o?: Record<string, unknown>) => string;

const yearOf = (today?: string) => (today ? Number(today.slice(0, 4)) : undefined);

export function themeTitle(t: T, year: number, name: string, today?: string): string {
  const now = yearOf(today);
  if (now === year) return t("memories.themeThisYear", { name });
  if (now === year + 1) return t("memories.themeLastYear", { name });
  return t("memories.themeTitle", { year, name });
}

function parts(d: string): [number, number, number] {
  const [y, m, dd] = d.split("-").map(Number);
  return [y!, m!, dd!];
}

export function eventTitle(t: T, day: string, last: string | null | undefined, name: string | null | undefined, today?: string): string {
  // the range says how many days (4/8–4/9): no 「（2 天）」 beside it (MEM-020 keeps its title)
  void t;
  const [y, m, d] = parts(day);
  const start = yearOf(today) === y ? `${m}/${d}` : `${y}/${m}/${d}`;
  let when = start;
  if (last) {
    const [, m2, d2] = parts(last);
    when = `${start}–${m2}/${d2}`;
  }
  return name ? `${when} · ${name}` : when;
}
