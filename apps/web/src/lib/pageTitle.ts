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

export function pageTitleKey(path: string): string | null {
  return PAGES.find(([re]) => re.test(path))?.[1] ?? null;
}

export function usePageArrival(path: string, t: (k: string) => string): void {
  const first = useRef(true);
  useEffect(() => {
    const k = pageTitleKey(path);
    document.title = k ? `${t(k)} — OpenPhotoAgent` : "OpenPhotoAgent";
    if (first.current) {
      first.current = false; // the app's first page: focus is where the app put it
      return;
    }
    // a page loaded on first visit draws its heading a moment later: look for it a few times
    let tries = 0;
    let timer = 0;
    const find = () => {
      const h = document.querySelector<HTMLElement>("#main h1");
      if (h) {
        if (!h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
        h.focus({ preventScroll: true });
      } else if (tries++ < 10) timer = window.setTimeout(find, 100);
    };
    find();
    return () => window.clearTimeout(timer);
  }, [path, t]);
}
