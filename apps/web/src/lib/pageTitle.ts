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
    if (!path.startsWith("/people")) {
      // the people wall comes back to the person you came from (FACE-UI-024), only from them: from anywhere else the
      // page's heading takes focus (VIEW-058)
      try {
        window.sessionStorage.removeItem("opa.people.last");
      } catch {
        /* nothing remembered */
      }
    }
    const k = pageTitleKey(path);
    const name = k ? t(k) : null;
    document.title = name ? `${name} — ${APP}` : APP;
    const heading = () => document.querySelector<HTMLElement>("#main h1");
    let timer = 0;
    // once on a page, keep up with its heading for a while: drawn late after a reload, or its names arriving
    // later (「2026 的activity.crafts」 → 「2026 的手工」); the window title follows it (VIEW-057)
    const settle = (left: number) => {
      const h = heading();
      if (h) {
        seen.current = h;
        const text = h.textContent?.trim();
        if (text && !first.current) document.title = `${text} — ${APP}`;
      }
      if (left > 0) timer = window.setTimeout(() => settle(left - 1), 50);
    };
    if (first.current) {
      settle(TRIES * 2); // the app's first page: focus is where the app put it
      first.current = false;
      return () => window.clearTimeout(timer);
    }
    // the old page's heading can still be there for a moment: focusing it dropped focus to BODY when it went
    // (VIEW-056). The new page's is a different element, or one already reading the new page's name.
    let tries = 0;
    const find = () => {
      const h = heading();
      if (h && (h !== seen.current || h.textContent?.trim() === name || tries >= TRIES)) {
        if (!h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
        h.focus({ preventScroll: true });
        settle(TRIES * 2);
      } else if (tries++ < TRIES) timer = window.setTimeout(find, 50);
    };
    find();
    return () => window.clearTimeout(timer);
  }, [path, t]);
}
