// Choosing photos on the timeline (audit round 50): Ctrl/⌘-click toggles one, Shift-click takes everything from the
// last photo picked to this one, as in Google Photos and Finder. `order` is the loaded photos in timeline order.
// Ctrl+A chooses the whole library (VIEW-042): the timeline loads only the months on screen, so "all" is a flag the
// server resolves as a query, and `ids` then holds the photos taken back out.
export type Selection = { ids: Set<string>; anchor: string | null; all?: boolean };
export const NONE: Selection = { ids: new Set(), anchor: null };

export const all = (): Selection => ({ ids: new Set(), anchor: null, all: true });

export function chosen(s: Selection, id: string): boolean {
  return s.all ? !s.ids.has(id) : s.ids.has(id);
}

export function countOf(s: Selection, total: number): number {
  return s.all ? Math.max(0, total - s.ids.size) : s.ids.size;
}

export function pick(sel: Selection, id: string, how: "toggle" | "range", order: string[]): Selection {
  const ids = new Set(sel.ids);
  // with everything chosen, `ids` is what was taken out: choosing a photo removes it from there, and the other way
  const choose = (x: string) => (sel.all ? ids.delete(x) : ids.add(x));
  const from = sel.anchor ? order.indexOf(sel.anchor) : -1;
  const to = order.indexOf(id);
  if (how === "range" && from >= 0 && to >= 0) {
    for (let i = Math.min(from, to); i <= Math.max(from, to); i++) choose(order[i]);
    return { ids, anchor: id, all: sel.all };
  }
  if (how === "range") choose(id);
  else if (ids.has(id)) ids.delete(id);
  else ids.add(id);
  return { ids, anchor: id, all: sel.all };
}
