// 那年今日 (10 §2.8): the same day in earlier years, a row per year at the top of 照片. Read-only; 「今天先不看」 hides it
// until tomorrow, on this computer only.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
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

function hiddenToday(day: string): boolean {
  try {
    return window.localStorage.getItem(KEY) === day;
  } catch {
    return false;
  }
}

export function OnThisDay({ onOpen, away = false }: { onOpen: (photos: MemoryPhoto[], start: number) => void; away?: boolean }) {
  const { t, i18n } = useTranslation();
  const q = useQuery({ queryKey: ["on-this-day"], queryFn: () => api.onThisDay(), staleTime: 60 * 60 * 1000 });
  const [hidden, setHidden] = useState(false);
  const day = q.data?.today ?? "";
  // `away`: the timeline has been scrolled down; the card steps aside and comes back at the top (MEM-009)
  if (!q.data?.groups.length || hidden || away || hiddenToday(day)) return null;
  return (
    <section aria-labelledby="otd-h" className="on-this-day">
      <div className="flex items-baseline gap-3">
        <h2 id="otd-h" className="font-semibold">{t("memories.onThisDay")}</h2>
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
          }}
        >
          {t("memories.notToday")}
        </button>
      </div>
      <div className="on-this-day-years">
        {q.data.groups.map((g) => (
          <div key={g.year} className="on-this-day-year">
            <p className="text-sm">
              <span className="font-medium">{t("memories.yearsAgo", { count: g.years_ago })}</span>{" "}
              <span className="tnum text-[var(--text-muted)]">{g.year}</span>{" "}
              {g.total && g.total > g.photos.length ? (
                // a few of them, said so: 「共 24 張」 read as all there were (MEM-006)
                <span className="tnum text-xs text-[var(--text-muted)]">{t("memories.picked", { n: g.photos.length.toLocaleString(i18n.language), total: g.total.toLocaleString(i18n.language) })}</span>
              ) : null}
            </p>
            <ul className="flex gap-1">
              {g.photos.slice(0, SHOWN).map((p, i) => (
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
              {g.photos.length > SHOWN ? (
                <li className="self-center">
                  {/* the rest open where the row stops (audit round 49: 「+16」 was only text) */}
                  <button
                    type="button"
                    className="on-this-day-more tnum"
                    aria-label={t("memories.more", { n: (g.photos.length - SHOWN).toLocaleString(i18n.language), year: g.year })}
                    onClick={() => onOpen(g.photos.map((x) => ({ id: x.asset_id, ch: x.ch ?? undefined, rot: x.rot })), SHOWN)}
                  >
                    +{(g.photos.length - SHOWN).toLocaleString(i18n.language)}
                  </button>
                </li>
              ) : null}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
