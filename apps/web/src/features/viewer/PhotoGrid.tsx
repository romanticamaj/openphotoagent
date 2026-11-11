// <PhotoGrid> (10 §2.2): month buckets with known counts are stacked by TimelineModel (estimated heights until
// laid out); TanStack Virtual decides which buckets mount, and inside a bucket only the rows near the viewport
// render (≤ ~400 tiles on a 4K screen). Layout runs in a Web Worker. ThumbHash paints every tile before its image.
import { Link } from "@tanstack/react-router";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useActivity } from "../activity/ActivityHost";
import { api, mediaUrl, type TimelineAsset } from "../../lib/api";
import { turnStyle } from "../../lib/turn";
import { placeholder } from "../../lib/thumbhash";
import type { Layout } from "./layout";
import { DAY_H, thumbVariant, visibleTiles } from "./layout";
import { layoutAsync } from "./layoutClient";
import { Scrubber } from "./Scrubber";
import { HEADER_H, TimelineModel, monthLabel, type Bucket } from "./timeline";

export type GridPos = { bucket: number; index: number };
/** Its own component: the activity query ticks every second, and the grid must not render with it. */
function EmptyLibrary() {
  const { t } = useTranslation();
  const working = useActivity().data?.some((o) => ["scan", "job.label"].includes(o.type)) ?? false;
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <p className="text-base font-semibold">{t(working ? "viewer.arriving" : "viewer.empty")}</p>
      {working ? null : (
        <>
          <p className="max-w-md text-[var(--text-muted)]">{t("viewer.emptyHint")}</p>
          <Link to="/import" className="rounded-[var(--radius-md)] bg-[var(--accent)] px-4 py-2 font-semibold text-[var(--accent-fg)]">
            {t("viewer.emptyAction")}
          </Link>
        </>
      )}
    </div>
  );
}

export type GridApi = {
  total: number;
  ordinal: (p: GridPos) => number;
  at: (ordinal: number) => Promise<{ asset: TimelineAsset; pos: GridPos } | null>;
};

const DENSITY = { comfortable: 260, standard: 200, compact: 120 } as const;
const FAST_SCREENS_PER_S = 4;
// the tiles below come from React state, a frame behind the browser's scroll: a wheel flick moves three screens in that
// frame. Only ThumbHash backgrounds are painted while scrolling fast, so reaching further ahead is cheap (VIEW-003).
const FAST_OVERSCAN_ROWS = 30;

function aspect(a: TimelineAsset): number {
  return a.w && a.h ? Math.min(Math.max(a.w / a.h, 0.25), 4) : 1.5;
}

export function PhotoGrid({
  density = "standard",
  onOpen,
  focusAsset,
}: {
  density?: keyof typeof DENSITY;
  onOpen: (pos: GridPos, api: GridApi) => void;
  focusAsset?: string | null;
}) {
  const { t, i18n } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [viewport, setViewport] = useState(800);
  const [scrollTop, setScrollTop] = useState(0);
  const [fast, setFast] = useState(false);
  const [, bump] = useState(0);
  const [focus, setFocus] = useState<GridPos>({ bucket: 0, index: 0 });
  const layouts = useRef(new Map<string, Layout>());
  const target = DENSITY[density];
  const qc = useQueryClient();

  const buckets = useQuery({ queryKey: ["timeline", "buckets"], queryFn: api.buckets });
  const list: Bucket[] = useMemo(() => buckets.data?.buckets ?? [], [buckets.data]);
  const hasPhotos = list.length > 0;
  const innerWidth = Math.max(0, width - 72); // scrubber gutter
  const model = useMemo(() => new TimelineModel(list, innerWidth || 1, target), [list, innerWidth, target]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setWidth(el.clientWidth);
      setViewport(el.clientHeight);
    });
    ro.observe(el);
    setWidth(el.clientWidth);
    setViewport(el.clientHeight);
    return () => ro.disconnect();
  }, [hasPhotos]); // the empty state has no scroller: measure again when the first photos arrive (IMP-014)

  const virt = useVirtualizer({
    count: list.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => model.height(i),
    overscan: 1,
  });

  // fast scroll: paint ThumbHash only, load images once scrolling settles for 120 ms (10 §2.2)
  const last = useRef({ y: 0, t: 0 });
  const settle = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onScroll = useCallback(() => {
    const el = scrollRef.current!;
    const now = performance.now();
    const v = Math.abs(el.scrollTop - last.current.y) / Math.max(1, now - last.current.t);
    last.current = { y: el.scrollTop, t: now };
    setScrollTop(el.scrollTop);
    if (v * 1000 > FAST_SCREENS_PER_S * el.clientHeight) setFast(true);
    if (settle.current) clearTimeout(settle.current);
    settle.current = setTimeout(() => setFast(false), 120);
  }, []);

  const items = virt.getVirtualItems();
  const mounted = items.map((v) => list[v.index]?.month).filter(Boolean) as string[];
  const bucketData = useQueries({
    queries: list.map((b) => ({
      queryKey: ["timeline", "bucket", b.month],
      queryFn: () => api.bucket(b.month),
      enabled: mounted.includes(b.month),
      staleTime: 60_000,
    })),
  });

  // lay out loaded buckets off the main thread; replace estimates with real heights. A pending layout is never
  // dropped by a re-render (that left months stuck empty); only unmounting discards results.
  const alive = useRef(true);
  useEffect(() => () => void (alive.current = false), []);
  const pendingLayout = useRef(new Map<string, Layout>());
  useEffect(() => {
    if (!innerWidth) return;
    list.forEach((b, i) => {
      const data = bucketData[i]?.data;
      const key = `${b.month}@${innerWidth}@${target}`;
      if (!data || layouts.current.has(key) || pendingLayout.current.has(key)) return;
      const placeholder: Layout = { rows: [], height: model.height(i) - HEADER_H, width: innerWidth };
      pendingLayout.current.set(key, placeholder);
      void layoutAsync(data.assets.map(aspect), innerWidth, target, dayGroups(data.assets)).then((l) => {
        pendingLayout.current.delete(key);
        if (!alive.current) return;
        layouts.current.set(key, l);
        const idx = list.findIndex((x) => x.month === b.month);
        if (idx >= 0) model.setHeight(idx, HEADER_H + l.height);
        virt.measure();
        bump((n) => n + 1);
      });
    });
  });

  const layoutOf = (i: number) => layouts.current.get(`${list[i]?.month}@${innerWidth}@${target}`);
  const assetsOf = (i: number) => bucketData[i]?.data?.assets;

  const gridApi: GridApi = useMemo(
    () => ({
      total: model.totalCount,
      ordinal: (p) => {
        let n = 0;
        for (let i = 0; i < p.bucket; i++) n += list[i].count;
        return n + p.index;
      },
      at: async (ord) => {
        if (ord < 0 || ord >= model.totalCount) return null;
        let b = 0;
        let n = ord;
        while (b < list.length && n >= list[b].count) n -= list[b++].count;
        const month = list[b].month;
        const data = await qc.fetchQuery({ queryKey: ["timeline", "bucket", month], queryFn: () => api.bucket(month) });
        const asset = data.assets[n];
        return asset ? { asset, pos: { bucket: b, index: n } } : null;
      },
    }),
    [list, model, qc],
  );

  // keep the Lightbox's current photo in view when it closes (focus restore)
  useEffect(() => {
    if (!focusAsset) return;
    for (let b = 0; b < list.length; b++) {
      const idx = assetsOf(b)?.findIndex((a) => a.id === focusAsset) ?? -1;
      if (idx >= 0) {
        setFocus({ bucket: b, index: idx });
        requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-asset="${focusAsset}"]`)?.focus());
        return;
      }
    }
  }, [focusAsset]);

  const moveFocus = (dir: "left" | "right" | "up" | "down") => {
    const l = layoutOf(focus.bucket);
    const assets = assetsOf(focus.bucket);
    if (!l || !assets) return;
    let { bucket, index } = focus;
    if (dir === "left" || dir === "right") {
      index += dir === "left" ? -1 : 1;
      if (index < 0 && bucket > 0) {
        bucket--;
        index = (assetsOf(bucket)?.length ?? 1) - 1;
      } else if (index >= assets.length && bucket < list.length - 1) {
        bucket++;
        index = 0;
      }
    } else {
      const r = l.rows.findIndex((row) => index >= row.tiles[0].index && index < row.tiles[0].index + row.count);
      const tile = l.rows[r]?.tiles.find((x) => x.index === index);
      const nr = l.rows[r + (dir === "up" ? -1 : 1)];
      if (nr && tile) {
        const cx = tile.x + tile.w / 2;
        index = nr.tiles.reduce((best, x) => (Math.abs(x.x + x.w / 2 - cx) < Math.abs(best.x + best.w / 2 - cx) ? x : best)).index;
      } else if (!nr && dir === "down" && bucket < list.length - 1) {
        bucket++;
        index = 0;
      } else if (!nr && dir === "up" && bucket > 0) {
        bucket--;
        index = 0;
      }
    }
    index = Math.max(0, index);
    setFocus({ bucket, index });
    const id = assetsOf(bucket)?.[index]?.id;
    if (bucket !== focus.bucket) virt.scrollToIndex(bucket, { align: "start" });
    requestAnimationFrame(() => {
      const el = document.querySelector<HTMLElement>(`[data-asset="${id}"]`);
      el?.focus();
      el?.scrollIntoView({ block: "nearest" });
    });
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    const el = scrollRef.current!;
    const keys: Record<string, () => void> = {
      ArrowLeft: () => moveFocus("left"),
      ArrowRight: () => moveFocus("right"),
      ArrowUp: () => moveFocus("up"),
      ArrowDown: () => moveFocus("down"),
      // the focused tile may scroll out and unmount: keep keyboard focus on the grid container itself
      PageUp: () => (el.focus({ preventScroll: true }), el.scrollBy({ top: -el.clientHeight })),
      PageDown: () => (el.focus({ preventScroll: true }), el.scrollBy({ top: el.clientHeight })),
      Home: () => (el.focus({ preventScroll: true }), el.scrollTo({ top: 0 })),
      End: () => (el.focus({ preventScroll: true }), el.scrollTo({ top: el.scrollHeight })),
      Enter: () => onOpen(focus, gridApi),
    };
    const f = keys[e.key];
    if (f) {
      e.preventDefault();
      f();
    }
  };

  const jumpTo = (bucket: number) => {
    virt.scrollToIndex(bucket, { align: "start" });
  };

  if (buckets.isSuccess && list.length === 0) {
    return <EmptyLibrary />;
  }

  // global row numbering for aria-rowindex (unlaid buckets count their estimated rows)
  const rowBase: number[] = [];
  let rows = 0;
  list.forEach((b, i) => {
    rowBase.push(rows);
    rows += 1 + (layoutOf(i)?.rows.length || Math.max(1, Math.round((model.height(i) - HEADER_H) / (target + 2))));
  });

  return (
    <div className="relative h-full">
      <div
        ref={scrollRef}
        className="timeline"
        onScroll={onScroll}
        role="grid"
        aria-label={t("viewer.grid")}
        aria-rowcount={rows}
        aria-readonly="true"
        onKeyDown={onKeyDown}
        tabIndex={-1}
        data-testid="photo-grid"
        // every mounted month has its photos and its real layout (tests and snapshots wait for this)
        data-settled={items.length > 0 && items.every((v) => assetsOf(v.index) && (layoutOf(v.index)?.rows.length ?? 0) > 0) ? "true" : "false"}
      >
        <div style={{ height: virt.getTotalSize(), position: "relative" }}>
          {items.map((v) => {
            const b = list[v.index];
            const l = layoutOf(v.index);
            const assets = assetsOf(v.index);
            const top = v.start;
            const label = monthLabel(b.month, i18n.language) ?? t("viewer.unknownDate");
            const local = scrollTop - top - HEADER_H;
            const tiles = l && assets ? visibleTiles(l, local, viewport, fast ? FAST_OVERSCAN_ROWS : 2) : [];
            const rowOf = new Map<number, number>();
            l?.rows.forEach((r, ri) => r.tiles.forEach((x) => rowOf.set(x.index, ri)));
            const byRow = new Map<number, typeof tiles>();
            for (const x of tiles) {
              const ri = rowOf.get(x.index)!;
              if (!byRow.has(ri)) byRow.set(ri, []);
              byRow.get(ri)!.push(x);
            }
            return (
              <section
                key={b.month}
                data-month={b.month}
                role="rowgroup"
                aria-label={label}
                style={{ position: "absolute", top, left: 0, width: innerWidth, height: v.size }}
              >
                <div className="bucket-header" role="row" aria-rowindex={rowBase[v.index] + 1}>
                  <span role="columnheader">{label}</span>
                  <span className="tnum text-[var(--text-muted)]">{t("viewer.count", { count: b.count })}</span>
                </div>
                {(l?.heads ?? []).map((h) => (
                  <div key={`d${h.first}`} className="day-header" style={{ position: "absolute", top: HEADER_H + h.y, left: 0, height: DAY_H }}>
                    {dayLabel(assets?.[h.first]?.taken_at ?? null, i18n.language) ?? t("viewer.unknownDate")}
                  </div>
                ))}
                {[...byRow].map(([ri, rowTiles]) => {
                  const r = l!.rows[ri];
                  return (
                    <div
                      key={ri}
                      role="row"
                      aria-rowindex={rowBase[v.index] + 2 + ri}
                      style={{ position: "absolute", top: HEADER_H + r.y, left: 0, height: r.height, width: innerWidth }}
                    >
                      {rowTiles.map((x) => {
                        const a = assets![x.index];
                        const isFocus = focus.bucket === v.index && focus.index === x.index;
                        const ph = placeholder(a.thumbhash);
                        return (
                          <button
                            type="button"
                            key={a.id}
                            role="gridcell"
                            className="tile"
                            data-asset={a.id}
                            tabIndex={isFocus ? 0 : -1}
                            aria-label={t("viewer.photoAlt", { date: a.taken_at?.slice(0, 10) ?? t("viewer.unknownDate") })}
                            style={{ left: x.x, width: x.w, height: r.height, backgroundImage: ph ? `url(${ph})` : undefined }}
                            onFocus={() => setFocus({ bucket: v.index, index: x.index })}
                            onClick={() => {
                              setFocus({ bucket: v.index, index: x.index });
                              onOpen({ bucket: v.index, index: x.index }, gridApi);
                            }}
                          >
                            {!fast && (
                              <img
                                src={mediaUrl(a.id, thumbVariant(x.w, r.height, window.devicePixelRatio || 1), a.ch)}
                                style={turnStyle(a.rot, x.w, r.height)}
                                alt=""
                                decoding="async"
                                loading="lazy"
                                draggable={false}
                                onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                              />
                            )}
                          </button>
                        );
                      })}
                    </div>
                  );
                })}
              </section>
            );
          })}
        </div>
      </div>
      {list.length > 0 && (
        <Scrubber model={model} current={model.indexAt(scrollTop)} onJump={jumpTo} />
      )}
    </div>
  );
}

/** Photos of one calendar day (local time) are one group; the timeline is newest first, so groups follow the list. */
export function dayGroups(assets: Array<{ taken_at: string | null }>): number[] {
  const out: number[] = [];
  let prev: string | null | undefined;
  for (const a of assets) {
    const d = a.taken_at ? localDay(a.taken_at) : null;
    if (out.length && d === prev) out[out.length - 1]++;
    else out.push(1);
    prev = d;
  }
  return out;
}

function localDay(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

export function dayLabel(iso: string | null, lang: string): string | null {
  if (!iso) return null;
  return new Intl.DateTimeFormat(lang, { month: "long", day: "numeric", weekday: "short" }).format(new Date(iso));
}
