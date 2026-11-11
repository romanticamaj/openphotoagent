// Choosing photos on the timeline (audit round 50): Ctrl/⌘-click toggles one, Shift-click takes everything from the
// last photo picked to this one, as in Google Photos and Finder. `order` is the loaded photos in timeline order.
export type Selection = { ids: Set<string>; anchor: string | null };
export const NONE: Selection = { ids: new Set(), anchor: null };

export function pick(sel: Selection, id: string, how: "toggle" | "range", order: string[]): Selection {
  const ids = new Set(sel.ids);
  const from = sel.anchor ? order.indexOf(sel.anchor) : -1;
  const to = order.indexOf(id);
  if (how === "range" && from >= 0 && to >= 0) {
    for (let i = Math.min(from, to); i <= Math.max(from, to); i++) ids.add(order[i]);
    return { ids, anchor: id };
  }
  if (how === "range") ids.add(id);
  else if (ids.has(id)) ids.delete(id);
  else ids.add(id);
  return { ids, anchor: id };
}
