// 活動 (MEM-017, ADR-0049 addendum 2): days of many photos as memory cards, 「2025/11/13 · 足球」. Read-only; a card opens
// its photos in the Lightbox, 「看全部」 the search for that day.
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api, mediaUrl } from "../../lib/api";
import { turnStyle } from "../../lib/turn";
import type { MemoryPhoto } from "./OnThisDay";

const parts = (iso: string) => iso.split("-").map(Number) as [number, number, number];

export function Events({ onOpen }: { onOpen: (photos: MemoryPhoto[], start: number) => void }) {
  const { t, i18n } = useTranslation();
  const q = useQuery({ queryKey: ["memory-events", i18n.language], queryFn: () => api.memoryEvents(i18n.language), staleTime: 10 * 60 * 1000 });
  const list = q.data?.events ?? [];
  if (!list.length) return null;
  return (
    <section aria-labelledby="events-h" className="space-y-2">
      <h2 id="events-h" className="font-semibold">{t("memories.events")}</h2>
      <ul className="memory-themes">
        {list.map((ev) => {
          const [y, m, d] = parts(ev.day);
          // an outing over days next to each other is one card (MEM-020)
          const [, m2, d2] = ev.last ? parts(ev.last) : [y, m, d];
          const day = ev.last ? `${y}/${m}/${d}–${m2}/${d2}` : `${y}/${m}/${d}`;
          const ask = ev.last ? `${y}年${m}月${d}日到${m2}月${d2}日` : `${y}年${m}月${d}日`;
          const title = ev.name ? `${day} · ${ev.name}` : day;
          const cover = ev.photos[0];
          return (
            <li key={ev.day} className="memory-theme">
              <button
                type="button"
                className="memory-theme-cover"
                aria-label={t("memories.themeOpen", { title, n: ev.count.toLocaleString(i18n.language) })}
                onClick={() => onOpen(ev.photos.map((p) => ({ id: p.asset_id, ch: p.ch ?? undefined, rot: p.rot })), 0)}
                // in the sideways row of a narrow window, a card reached by Tab comes into view (audit round 100)
                onFocus={(e) => e.currentTarget.scrollIntoView?.({ block: "nearest", inline: "nearest" })}
              >
                {cover ? <img src={mediaUrl(cover.asset_id, "t512", cover.ch)} alt="" loading="lazy" style={turnStyle(cover.rot)} /> : null}
              </button>
              <p className="memory-theme-title text-sm font-medium" title={title}>{title}</p>
              <p className="flex items-baseline gap-2 text-xs">
                <span className="tnum text-[var(--text-muted)]">
                  {t("memories.themeCount", { n: ev.count.toLocaleString(i18n.language), k: ev.photos.length.toLocaleString(i18n.language) })}
                </span>
                <Link to="/search" search={{ q: ask }} className="ml-auto underline">
                  {t("memories.themeAll")}
                </Link>
              </p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
