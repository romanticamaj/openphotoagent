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
  const seen = useRef<HTMLElement | null>(null); // the heading of the page we are leaving
  useEffect(() => {
    const k = pageTitleKey(path);
    const name = k ? t(k) : null;
    document.title = name ? `${name} — ${APP}` : APP;
    const heading = () => document.querySelector<HTMLElement>("#main h1");
    if (first.current) {
      first.current = false; // the app's first page: focus is where the app put it
      seen.current = heading();
      return;
    }
    // the old page's heading can still be there for a moment: focusing it dropped focus to BODY when it went
    // (VIEW-056). The new page's is a different element, or one already reading the new page's name.
    let tries = 0;
    let timer = 0;
    const arrive = (h: HTMLElement) => {
      seen.current = h;
      const text = h.textContent?.trim();
      if (text) document.title = `${text} — ${APP}`; // a theme's page is 「2026 的手工」, not 「搜尋」
      if (!h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
      h.focus({ preventScroll: true });
    };
    const find = () => {
      const h = heading();
      if (h && (h !== seen.current || h.textContent?.trim() === name || tries >= TRIES)) arrive(h);
      else if (tries++ < TRIES) timer = window.setTimeout(find, 50);
    };
    find();
    return () => window.clearTimeout(timer);
  }, [path, t]);
}
