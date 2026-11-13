// The sidebar as a drawer in a narrow window (VIEW-049): a 「選單」 button opens it over the page; Esc, the scrim or
// following a link closes it. Wide windows show the sidebar as before; CSS decides which, and a window that widens
// closes the drawer (VIEW-050). Focus goes into the drawer when it opens and back to 「選單」 when it closes.
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

export const NARROW = "(max-width: 720px)";

type Refs = { toggle?: RefObject<HTMLElement | null>; panel?: RefObject<HTMLElement | null> };

export function useNavDrawer(path: string, refs: Refs = {}): { open: boolean; toggle: () => void; close: () => void } {
  const [open, setOpen] = useState(false);
  const wasOpen = useRef(false);
  const close = useCallback(() => setOpen(false), []);
  useEffect(() => setOpen(false), [path]);
  // focus follows the drawer (VIEW-050): in when it opens, back to 「選單」 when it closes
  useEffect(() => {
    if (open) refs.panel?.current?.querySelector<HTMLElement>("a[href], button")?.focus();
    else if (wasOpen.current) refs.toggle?.current?.focus();
    wasOpen.current = open;
  }, [open, refs.panel, refs.toggle]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    // a link to the page you are on changes no path: any link followed closes the drawer
    const onClick = (e: Event) => {
      if ((e.target as HTMLElement | null)?.closest("a")) setOpen(false);
    };
    const panel = refs.panel?.current;
    window.addEventListener("keydown", onKey);
    panel?.addEventListener("click", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      panel?.removeEventListener("click", onClick);
    };
  }, [open, refs.panel]);
  // a window that widens shows the sidebar in place: the drawer is closed (VIEW-050)
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(NARROW);
    const on = (e: { matches: boolean }) => {
      if (!e.matches) setOpen(false); // no longer narrow
    };
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return { open, toggle: () => setOpen((x) => !x), close };
}
