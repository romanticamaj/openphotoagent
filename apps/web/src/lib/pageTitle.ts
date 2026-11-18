// Each page says where you are (VIEW-054): the window title follows the page, and arriving at a page puts focus on
// its heading, so a screen reader says it and Tab starts there; the title said 「OpenPhotoAgent」 everywhere and
// focus stayed on BODY after 看全部 (audit round 89).
import { useEffect, useRef } from "react";

const PAGES: Array<[RegExp, string]> = [
  [/^\/$/, "nav.photos"],
  [/^\/albums/, "nav.albums"],
  [/^\/explore/, "nav.explore"],
  [/^\/people/, "nav.people"],
  [/^\/map/, "nav.map"],
  [/^\/import/, "nav.import"],
  [/^\/organize/, "nav.organize"],
  [/^\/plans/, "nav.organize"],
  [/^\/review/, "nav.review"],
  [/^\/duplicates/, "nav.duplicates"],
  [/^\/search/, "nav.search"],
  [/^\/models/, "nav.models"],
  [/^\/system/, "nav.doctor"],
  [/^\/settings/, "nav.settings"],
];

const APP = "OpenPhotoAgent";
const TRIES = 20; // × 50 ms: a page loaded on first visit draws its heading a moment later

export function pageTitleKey(path: string): string | null {
  return PAGES.find(([re]) => re.test(path))?.[1] ?? null;
}

export function usePageArrival(path: string, t: (k: string) => string): void {
  const first = useRef(true);
  const seen = useRef<HTMLElement | null>(null); // the heading of the page we are on: the one to leave behind
  useEffect(() => {
    const pathname = path.split("?")[0];
    if (!pathname.startsWith("/people")) {
      // the people wall comes back to the person you came from (FACE-UI-024), only from them: from anywhere else the
      // page's heading takes focus (VIEW-058)
      try {
        window.sessionStorage.removeItem("opa.people.last");
      } catch {
        /* nothing remembered */
      }
    }
    const k = pageTitleKey(pathname);
    const name = k ? t(k) : null;
    document.title = name ? `${name} — ${APP}` : APP;
    const heading = () => document.querySelector<HTMLElement>("#main h1");
    const stale = heading();
    const before = document.activeElement;
    let titleTimer = 0;
    let focusTimer = 0;
    // the window title follows the page's heading for a while: drawn late after a reload, its names arriving later
    // (「2026 的activity.crafts」 → 「2026 的手工」), or the same heading with new words after Back (VIEW-057/059)
    const follow = (left: number, now = false) => {
      const h = heading();
      const text = h?.textContent?.trim();
      // not at the very start of the app's first page: its name until the heading has been drawn and settled
      if (h && text && !(now && first.current && h === stale)) document.title = `${text} — ${APP}`;
      // a page with no heading of its own keeps its name, not the last page's words (VIEW-060)
      else if (!h && !now) document.title = name ? `${name} — ${APP}` : APP;
      if (left > 0) titleTimer = window.setTimeout(() => follow(left - 1), 50);
    };
    if (first.current) {
      // the app's first page: focus is where the app put it; its heading is what we leave next time
      const remember = (left: number) => {
        seen.current = heading() ?? seen.current;
        if (left > 0) focusTimer = window.setTimeout(() => remember(left - 1), 50);
      };
      follow(TRIES * 2, true);
      remember(TRIES * 2);
      first.current = false;
      return () => {
        window.clearTimeout(titleTimer);
        window.clearTimeout(focusTimer);
      };
    }
    follow(TRIES * 2, true);
    // focus: the new page's heading, not the old one on its way out (VIEW-056); unless the page placed focus itself in
    // its content, as the people wall does on the person you came back from (VIEW-059)
    let tries = 0;
    const find = () => {
      const h = heading();
      if (h && (h !== seen.current || h.textContent?.trim() === name || tries >= TRIES)) {
        seen.current = h;
        const now = document.activeElement;
        const placed = now !== before && now !== h && now instanceof HTMLElement && Boolean(document.getElementById("main")?.contains(now));
        if (placed) return;
        if (!h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
        h.focus({ preventScroll: true });
      } else if (tries++ < TRIES) focusTimer = window.setTimeout(find, 50);
    };
    find();
    return () => {
      window.clearTimeout(titleTimer);
      window.clearTimeout(focusTimer);
    };
  }, [path, t]);
}
