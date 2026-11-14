// 主題 (10 §2.8, MEM-011): a year's frequent subjects as memory cards, 「2025 的足球」. Read-only; a card opens its
// photos in the Lightbox, 「看全部」 the search for that year and subject.
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api, mediaUrl } from "../../lib/api";
import { turnStyle } from "../../lib/turn";
import type { StoryAll } from "./Events";
import type { MemoryPhoto } from "./OnThisDay";

export function Themes({ onOpen }: { onOpen: (photos: MemoryPhoto[], start: number, play?: boolean, title?: string, all?: StoryAll) => void }) {
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
                onFocus={(e) => e.currentTarget.scrollIntoView?.({ block: "nearest", inline: "nearest" })}
              >
                {cover ? <img src={mediaUrl(cover.asset_id, "t512", cover.ch)} alt="" loading="lazy" style={turnStyle(cover.rot)} /> : null}
              </button>
              {/* the title has a line of its own: beside the count and the link it was cut to 「2026 的…」 (audit round 82) */}
              <p className="memory-theme-title text-sm font-medium" title={title}>{title}</p>
              <p className="flex items-baseline gap-2 text-xs">
                <span className="tnum text-[var(--text-muted)]">
                  {t("memories.themeCount", { n: th.count.toLocaleString(i18n.language), k: th.photos.length.toLocaleString(i18n.language) })}
                </span>
                <button
                  type="button"
                  className="memory-play ml-auto"
                  aria-label={t("memories.play", { title })}
                  // the photos as a story: the Lightbox opens with its slideshow running (MEM-024, audit round 109)
                  onClick={() => onOpen(th.photos.map((p) => ({ id: p.asset_id, ch: p.ch ?? undefined, rot: p.rot })), 0, true, title, { n: th.count, search: { q: "", tag: th.key, year: String(th.year), name: th.name } })}
                >
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true">
                    <path d="M8 5.5v13l10.5-6.5z" />
                  </svg>
                  {t("memories.playShort")}
                </button>
                <Link to="/search" search={{ q: "", tag: th.key, year: String(th.year), name: th.name }} className="underline">
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
