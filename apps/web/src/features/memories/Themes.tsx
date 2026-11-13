// 主題 (10 §2.8, MEM-011): a year's frequent subjects as memory cards, 「2025 的足球」. Read-only; a card opens its
// photos in the Lightbox, 「看全部」 the search for that year and subject.
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api, mediaUrl } from "../../lib/api";
import { turnStyle } from "../../lib/turn";
import type { MemoryPhoto } from "./OnThisDay";

export function Themes({ onOpen }: { onOpen: (photos: MemoryPhoto[], start: number) => void }) {
  const { t, i18n } = useTranslation();
  const q = useQuery({ queryKey: ["memory-themes", i18n.language], queryFn: () => api.memoryThemes(i18n.language), staleTime: 10 * 60 * 1000 });
  const list = q.data?.themes ?? [];
  if (!list.length) return null;
  return (
    <section aria-labelledby="themes-h" className="space-y-2">
      <h2 id="themes-h" className="font-semibold">{t("memories.themes")}</h2>
      <ul className="memory-themes">
        {list.map((th) => {
          const title = t("memories.themeTitle", { year: th.year, name: th.name });
          const cover = th.photos[0];
          return (
            <li key={`${th.year}-${th.key}`} className="memory-theme">
              <button
                type="button"
                className="memory-theme-cover"
                aria-label={t("memories.themeOpen", { title, n: th.count.toLocaleString(i18n.language) })}
                onClick={() => onOpen(th.photos.map((p) => ({ id: p.asset_id, ch: p.ch ?? undefined, rot: p.rot })), 0)}
              >
                {cover ? <img src={mediaUrl(cover.asset_id, "t512", cover.ch)} alt="" loading="lazy" style={turnStyle(cover.rot)} /> : null}
              </button>
              <p className="flex items-baseline gap-2 text-sm">
                <span className="font-medium">{title}</span>
                <span className="tnum text-[var(--text-muted)]">{t("memories.themeCount", { n: th.count.toLocaleString(i18n.language) })}</span>
                <Link to="/search" search={{ q: `${th.year}年 ${th.name}` }} className="ml-auto text-xs underline">
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
