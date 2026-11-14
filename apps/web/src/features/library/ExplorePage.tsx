// 分類 (ADR-0029): what the AI found, at a glance. Categories and tags with their counts, biggest first; choosing one
// narrows the photos below. Filtering only: nothing here changes a label or a file.
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { api, mediaUrl, type SearchBody } from "../../lib/api";
import { memoryGrid } from "../memories/OnThisDay";
import { Events } from "../memories/Events";
import { Themes } from "../memories/Themes";
import { Lightbox } from "../viewer/Lightbox";
import type { GridApi } from "../viewer/PhotoGrid";

type Pick = { kind: "category" | "tag"; value: string } | null;
const TOP_TAGS = 16;
const PAGE = 120;
const byCount = (m: Record<string, number> = {}) => Object.entries(m).sort((a, b) => b[1] - a[1]);

// words that describe almost every photo of a room or a pose: also on the vocabulary blacklist, so new analyses drop them
const GENERIC = new Set(["地板", "木地板", "牆", "牆壁", "天花板", "背景", "坐姿", "穿著", "動作", "線條", "彩色", "手", "人", "互動", "活動", "燈光"]);

export type ExplorePick = Pick;

/** ``initial`` and ``onPick`` keep the choice in the URL (IMP-025): a category can be linked and survives a reload. */
export function ExplorePage({ initial = null, onPick }: { initial?: Pick; onPick?: (p: Pick) => void } = {}) {
  const { t, i18n } = useTranslation();
  const [pick, setPickState] = useState<Pick>(initial);
  const setPick = (p: Pick) => {
    setPickState(p);
    onPick?.(p);
  };
  const [viewing, setViewing] = useState<number | null>(null);
  const [memory, setMemory] = useState<{ start: number; grid: GridApi; play?: boolean; title?: string } | null>(null);
  const preset = useQuery({ queryKey: ["preset", "by-content-12", i18n.language], queryFn: () => api.preset("by-content-12", i18n.language) });
  const names = useMemo(() => new Map((preset.data?.categories ?? []).map((c) => [c.key, c.name])), [preset.data]);
  const all = useQuery({
    queryKey: ["explore", "facets"],
    // every photo counts: folding copies hid 180 analysed photos (20 groups of 10) from 「已分析」 (IMP-039)
    queryFn: () => api.search({ q: null, limit: 1, facets: ["category", "tags"], lang: i18n.language, collapse_duplicates: false }),
    refetchInterval: 5000, // counts grow while a folder is being analysed
  });
  const library = useQuery({ queryKey: ["timeline", "buckets"], queryFn: api.buckets });
  const filters: SearchBody["filters"] = pick?.kind === "category" ? { category: pick.value } : pick?.kind === "tag" ? { tags_all: [pick.value] } : {};
  const res = useInfiniteQuery({
    queryKey: ["explore", "photos", pick],
    queryFn: ({ pageParam }) => api.search({ q: null, filters, limit: PAGE, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const hits = (res.data?.pages ?? []).flatMap((p) => p.items);
  // photos added before analysis was automatic: photos already labelled come back from the cache (Q-014)
  const rest = useMutation({
    mutationFn: async () => {
      for (const s of await api.sources()) await api.analyzeSource(s.id);
    },
  });
  const grid: GridApi = useMemo(
    () => ({
      total: hits.length,
      ordinal: (p) => p.index,
      at: async (i) => {
        const h = hits[i];
        if (!h) return null;
        return { asset: { id: h.asset_id, ch: h.ch, w: null, h: null, thumbhash: null, taken_at: h.taken_at, flags: [] }, pos: { bucket: 0, index: i } };
      },
    }),
    [hits],
  );
  const categories = byCount(all.data?.facets.category);
  // concepts first, then free tags, never the generic ones (IMP-020, IMP-026): those stay searchable
  const named = (k: string) => all.data?.tag_names?.[k] !== undefined;
  const tags = byCount(all.data?.facets.tags)
    .filter(([k]) => !GENERIC.has(k))
    .sort((a, b) => Number(named(b[0])) - Number(named(a[0])))
    .slice(0, TOP_TAGS);
  const analysed = categories.reduce((n, [, c]) => n + c, 0);
  const total = (library.data?.buckets ?? []).reduce((n, b) => n + b.count, 0);
  const left = Math.max(0, total - analysed);
  const num = (n: number) => n.toLocaleString(i18n.language);
  // what was picked, named over the photos with its count (IMP-038)
  const pickedName = pick ? (pick.kind === "category" ? (names.get(pick.value) ?? pick.value) : (all.data?.tag_names?.[pick.value] ?? pick.value)) : "";
  const pickedCount = pick ? ((pick.kind === "category" ? all.data?.facets.category : all.data?.facets.tags)?.[pick.value] ?? 0) : 0;
  const chip = (next: Pick, label: string, count: number) => {
    const on = pick?.kind === next?.kind && pick?.value === next?.value;
    return (
      <button key={`${next?.kind}-${next?.value}`} type="button" aria-pressed={on} className="chip" onClick={() => setPick(on ? null : next)}>
        {label}
        <span className="tnum chip-count">{num(count)}</span>
      </button>
    );
  };
  if (all.isSuccess && analysed === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <h1 className="text-base font-semibold">{t("explore.none")}</h1>
        <p className="max-w-md text-[var(--text-muted)]">{t("explore.noneHint")}</p>
        <Link to="/import" className="rounded-[var(--radius-md)] bg-[var(--accent)] px-4 py-2 font-semibold text-[var(--accent-fg)]">
          {t("viewer.emptyAction")}
        </Link>
      </div>
    );
  }
  return (
    <div className="space-y-4 p-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">{t("explore.title")}</h1>
        <p data-testid="explore-summary" className="text-[var(--text-muted)]">
          {t("explore.analysed", { n: num(analysed) })}
          {left ? ` ${t("explore.left", { n: num(left) })}` : ""}
        </p>
        {left ? (
          <Button variant="ghost" disabledReason={rest.isPending || rest.isSuccess ? { code: "BUSY", message: t("explore.started") } : null} onClick={() => rest.mutate()}>
            {t("explore.analyseRest", { n: num(left) })}
          </Button>
        ) : null}
      </header>
      {/* a year's frequent subjects, before the counts (MEM-011) */}
      {!pick ? <Events onOpen={(photos, start, play, title) => setMemory({ start, grid: memoryGrid(photos), play, title })} /> : null}
      {!pick ? <Themes onOpen={(photos, start, play, title) => setMemory({ start, grid: memoryGrid(photos), play, title })} /> : null}
      {/* each row says what it is: 類別 (one per photo) and 標籤 (any number) were two unlabelled rows (IMP-038) */}
      <section aria-labelledby="explore-cat-h" className="explore-row">
        <h2 id="explore-cat-h" className="explore-row-h">{t("explore.categoryRow")}</h2>
        <div className="flex flex-wrap gap-2">{categories.map(([k, n]) => chip({ kind: "category", value: k }, names.get(k) ?? k, n))}</div>
      </section>
      {tags.length > 0 ? (
        <section aria-labelledby="explore-tag-h" className="explore-row">
          <h2 id="explore-tag-h" className="explore-row-h">{t("explore.tags")}</h2>
          <div className="flex flex-wrap gap-2 text-sm">{tags.map(([k, n]) => chip({ kind: "tag", value: k }, all.data?.tag_names?.[k] ?? k, n))}</div>
        </section>
      ) : null}
      {pick ? (
        <h2 className="text-lg font-semibold">
          {pickedName} <span className="tnum text-sm font-normal text-[var(--text-muted)]">{t("albums.count", { n: num(pickedCount) })}</span>
        </h2>
      ) : null}
      <div className="explore-grid">
        {hits.map((h, i) => (
          <button key={h.asset_id} type="button" className="explore-tile" onClick={() => setViewing(i)}>
            <img src={mediaUrl(h.asset_id, "t256", h.ch)} alt="" loading="lazy" />
            <span className="explore-caption">{h.caption ?? (h.category ? (names.get(h.category) ?? h.category) : t("explore.photo"))}</span>
          </button>
        ))}
      </div>
      {res.hasNextPage ? (
        <Button variant="ghost" disabledReason={res.isFetchingNextPage ? { code: "BUSY", message: t("common.working") } : null} onClick={() => void res.fetchNextPage()}>
          {t("explore.more")}
        </Button>
      ) : null}
      {viewing !== null ? <Lightbox start={viewing} grid={grid} onClose={() => setViewing(null)} /> : null}
      {memory ? <Lightbox start={memory.start} grid={memory.grid} autoplay={memory.play} title={memory.title} onClose={() => setMemory(null)} /> : null}
    </div>
  );
}
