// What a duplicate group's card shows (SRCH-DUP-009): the copy kept first, then up to `max` in all, and how many more.
// A group of ten in one row pushed out of its card and scrolled the page sideways (audit round 53). An exact group's
// members are one photo's locations: they share the id, and the first of them is the one kept.
export function cardMembers<M extends { asset_id: string }>(members: M[], rep: string, max: number): { shown: M[]; more: number } {
  const at = Math.max(0, members.findIndex((m) => m.asset_id === rep));
  const ordered = [members[at], ...members.slice(0, at), ...members.slice(at + 1)].filter(Boolean);
  return { shown: ordered.slice(0, max), more: Math.max(0, ordered.length - max) };
}
