// 那年今日 (10 §2.8): the same day in earlier years, a row per year at the top of 照片. Read-only; 「今天先不看」 hides it
// until tomorrow, on this computer only.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, mediaUrl } from "../../lib/api";
import { turnStyle } from "../../lib/turn";
import type { GridApi } from "../viewer/PhotoGrid";

export type MemoryPhoto = { id: string; ch?: string; rot?: number };

/** A Lightbox over a year's photos (10 §2.8). Each keeps its hash: the desktop app finds a thumbnail by it, and the
 * photos the row never drew had none (MEM-008: 「+16」 opened broken images). */
export function memoryGrid(photos: MemoryPhoto[]): GridApi {
  return {
    total: photos.length,
    ordinal: (p) => p.index,
    at: async (i) =>
      photos[i]
        ? { asset: { id: photos[i].id, ch: photos[i].ch, w: null, h: null, thumbhash: null, taken_at: null, flags: [], rot: photos[i].rot }, pos: { bucket: 0, index: i } }
        : null,
  };
}

const KEY = "opa.memories.hidden";
const SHOWN = 8; // photos per year in the row; the rest open in the Lightbox
// fewer each when there are more years, so all fit: at 1280 px the third was cut off (round 136, MEM-031)
const shownFor = (years: number) => Math.min(SHOWN, Math.max(4, Math.floor(12 / Math.max(1, years))));
const TILE = 76; // a 72 px tile and its 4 px gap
const YEAR_EXTRA = 52; // a year's 「+N」 and the gap before it
const YEAR_GAP = 24;

/** Photos a year, from the card's width so every year is on one row (round 138, MEM-036): at 1024 px the third year
 *  wrapped and the card grew to 252 px. Never fewer than 2 (then the years wrap, MEM-034); unmeasured, by count. */
export function tilesFor(width: number | undefined, years: number): number {
  const most = shownFor(years);
  if (!width) return most;
  const n = Math.floor((width - (years - 1) * YEAR_GAP - years * YEAR_EXTRA) / (years * TILE));
  return Math.max(2, Math.min(most, n));
}

const HEAD = 150; // a year's two-line heading (MEM-043)

/** A year's width with ``n`` of its ``count`` photos shown, and 「+N」 when some are not. */
export function yearWidth(n: number, count: number): number {
  return Math.max(HEAD, n * TILE + (count > n ? YEAR_EXTRA : 0));
}

/** Photos a year, the room left given to the year with the most still unshown (round 140, MEM-049): the latest year
 *  showed 2-3 while 230-410 px stayed empty. A year shows at least 1 (2 when it has them), at most 8. */
export function allocate(width: number | undefined, counts: number[]): number[] {
  if (!width) return counts.map((c) => Math.min(c, shownFor(counts.length)));
  const per = counts.map((c) => Math.min(c, 2));
  const total = () => per.reduce((a, n, i) => a + yearWidth(n, counts[i]!), 0) + (per.length - 1) * YEAR_GAP;
  for (;;) {
    const room = (i: number) => counts[i]! - per[i]!;
    const order = per.map((_, i) => i).filter((i) => per[i]! < Math.min(counts[i]!, SHOWN)).sort((a, b) => room(b) - room(a));
    let grew = false;
    for (const i of order) {
      per[i]! += 1;
      if (total() <= width) {
        grew = true;
        break;
      }
      per[i]! -= 1;
    }
    if (!grew) return per;
  }
}

function hiddenToday(day: string): boolean {
  try {
    return window.localStorage.getItem(KEY) === day;
  } catch {
    return false;
  }
}

export function OnThisDay({ onOpen, away = false }: { onOpen: (photos: MemoryPhoto[], start: number) => void; away?: boolean }) {
  const { t, i18n } = useTranslation();
  // asked once the page is idle, after the timeline's first photos: with the card asked at once, the first photo
  // came at 266 ms against 196 ms (round 136, MEM-032)
  const [idle, setIdle] = useState(false);
  useEffect(() => {
    const w = window as Window & { requestIdleCallback?: (f: () => void, o?: { timeout: number }) => number; cancelIdleCallback?: (id: number) => void };
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(() => setIdle(true), { timeout: 1500 });
      return () => w.cancelIdleCallback?.(id);
    }
    const id = window.setTimeout(() => setIdle(true), 300);
    return () => window.clearTimeout(id);
  }, []);
  const q = useQuery({ queryKey: ["on-this-day"], queryFn: () => api.onThisDay(), staleTime: 60 * 60 * 1000, enabled: idle });
  const [hidden, setHidden] = useState(false);
  const day = q.data?.today ?? "";
  const month = q.data?.scope === "month";
  const [width, setWidth] = useState<number | undefined>(undefined);
  const box = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setWidth(e?.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  });
  const perYear = allocate(width, (q.data?.groups ?? []).map((g) => g.photos.length));
  // `away`: the timeline has been scrolled down; the card steps aside and comes back at the top (MEM-009)
  if (!q.data?.groups.length || hidden || away || hiddenToday(day)) return null;
  return (
    <section ref={box} aria-labelledby="otd-h" className="on-this-day">
      <div className="flex items-baseline gap-3">
        {/* none near the day: the same month, said so (round 135, MEM-027) */}
        <h2 id="otd-h" className="font-semibold">{t(month ? "memories.thisMonth" : "memories.onThisDay")}</h2>
        <button
          type="button"
          className="ml-auto text-xs text-[var(--text-muted)] underline"
          onClick={() => {
            try {
              window.localStorage.setItem(KEY, day);
            } catch {
              /* not remembered: it comes back on the next visit */
            }
            setHidden(true);
            // the button goes with the card: the timeline takes the keyboard, not the page (round 137, MEM-035)
            // its current photo, not the grid itself: → then skipped to photo 2 (round 138, MEM-035 revised)
            requestAnimationFrame(() => {
              const grid = document.querySelector<HTMLElement>('[role="grid"]');
              (grid?.querySelector<HTMLElement>('[tabindex="0"]') ?? grid)?.focus();
            });
          }}
        >
          {t("memories.notToday")}
        </button>
      </div>
      <div className="on-this-day-years">
        {q.data.groups.map((g, gi) => {
          const per = perYear[gi] ?? SHOWN;
          return (
          <div key={g.year} className="on-this-day-year">
            {/* two lines: on one, 247 px a year and three did not fit at 1024 px (round 139, MEM-043) */}
            <p className="on-this-day-year-head text-sm">
              <span className="font-medium">{t(month ? "memories.monthsAgo" : "memories.yearsAgo", { count: g.years_ago })}</span>
              <span className="tnum text-xs text-[var(--text-muted)]">
                {g.year}
                {g.total && g.total > g.photos.length ? (
                  // a few of them, said so: 「共 24 張」 read as all there were (MEM-006)
                  <> · {t("memories.picked", { n: g.photos.length.toLocaleString(i18n.language), total: g.total.toLocaleString(i18n.language) })}</>
                ) : null}
              </span>
            </p>
            <ul className="flex gap-1">
              {g.photos.slice(0, per).map((p, i) => (
                <li key={p.asset_id}>
                  <button
                    type="button"
                    className="on-this-day-tile"
                    aria-label={t("memories.open", { year: g.year, n: (i + 1).toLocaleString(i18n.language) })}
                    onClick={() => onOpen(g.photos.map((x) => ({ id: x.asset_id, ch: x.ch ?? undefined, rot: x.rot })), i)}
                  >
                    <img src={mediaUrl(p.asset_id, "t256", p.ch)} alt="" loading="lazy" style={turnStyle(p.rot)} />
                  </button>
                </li>
              ))}
              {g.photos.length > per ? (
                <li className="self-center">
                  {/* the rest open where the row stops (audit round 49: 「+16」 was only text) */}
                  <button
                    type="button"
                    className="on-this-day-more tnum"
                    aria-label={t("memories.more", { n: (g.photos.length - per).toLocaleString(i18n.language), year: g.year })}
                    onClick={() => onOpen(g.photos.map((x) => ({ id: x.asset_id, ch: x.ch ?? undefined, rot: x.rot })), per)}
                  >
                    +{(g.photos.length - per).toLocaleString(i18n.language)}
                  </button>
                </li>
              ) : null}
            </ul>
          </div>
          );
        })}
      </div>
    </section>
  );
}
