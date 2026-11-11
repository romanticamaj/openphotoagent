// 那年今日 (10 §2.8): the same day in earlier years, a row per year at the top of 照片. Read-only; 「今天先不看」 hides it
// until tomorrow, on this computer only.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, mediaUrl } from "../../lib/api";

const KEY = "opa.memories.hidden";
const SHOWN = 8; // photos per year in the row; the rest open in the Lightbox

function hiddenToday(day: string): boolean {
  try {
    return window.localStorage.getItem(KEY) === day;
  } catch {
    return false;
  }
}

export function OnThisDay({ onOpen }: { onOpen: (ids: string[], start: number) => void }) {
  const { t, i18n } = useTranslation();
  const q = useQuery({ queryKey: ["on-this-day"], queryFn: () => api.onThisDay(), staleTime: 60 * 60 * 1000 });
  const [hidden, setHidden] = useState(false);
  const day = q.data?.today ?? "";
  if (!q.data?.groups.length || hidden || hiddenToday(day)) return null;
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
              <span className="tnum text-[var(--text-muted)]">{g.year}</span>
            </p>
            <ul className="flex gap-1">
              {g.photos.slice(0, SHOWN).map((p, i) => (
                <li key={p.asset_id}>
                  <button
                    type="button"
                    className="on-this-day-tile"
                    aria-label={t("memories.open", { year: g.year, n: (i + 1).toLocaleString(i18n.language) })}
                    onClick={() => onOpen(g.photos.map((x) => x.asset_id), i)}
                  >
                    <img src={mediaUrl(p.asset_id, "t256", p.ch)} alt="" loading="lazy" />
                  </button>
                </li>
              ))}
              {g.photos.length > SHOWN ? (
                <li className="self-center text-xs text-[var(--text-muted)]">+{(g.photos.length - SHOWN).toLocaleString(i18n.language)}</li>
              ) : null}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
