// Search (05 §2, 10 §6): keyword + text-to-image results with tag/year facets as removable chips, an indexing
// banner while vectors are being built, and selection → batch actions. "Select all" is a query handle (⌘A).
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { COMPARE_MAX, CompareView } from "./CompareView";
import { Lightbox } from "../viewer/Lightbox";
import type { GridApi } from "../viewer/PhotoGrid";
import { api, isDegraded, mediaUrl, type PossibleHit, type SearchBody } from "../../lib/api";
import { turnStyle } from "../../lib/turn";

import { AddToAlbum, FavoriteSelection, typingIn } from "../albums/AlbumButtons";
import { askAbout } from "../../lib/askBus";
import { CopyToFolderButton } from "../../components/CopyToFolder";
import { TrashButton } from "../../components/TrashButton";
import { bodyFor, cappedNote, chipLabel, chipsFrom, dateWords, gridAssetOf, headerCount, loadMoreText, onlyByMeaning, relaxedText, softView, tileLabel, type Chip, weakNeedsMore, weakTail, withoutPerson, withoutWords } from "./searchPlan";

const sameChips = (a: Chip[], b: Chip[]) => a.length === b.length && a.every((c, i) => JSON.stringify(c) === JSON.stringify(b[i]));

/** tries for a search page beyond the first: the page says it failed instead of waiting through three (SRCH-UI-047) */
export const searchRetry = 1;

export function SearchPage({ q, tag, year, name }: { q: string; tag?: string; year?: string; name?: string }) {
  const { t, i18n } = useTranslation();
  const nav = useNavigate();
  // a theme's 「看全部」 asks by its tag and year, not by words (SRCH-UI-037)
  const seed = useMemo<Chip[]>(() => [...(tag ? [{ kind: "tag" as const, value: tag }] : []), ...(year ? [{ kind: "year" as const, value: year }] : [])], [tag, year]);
  const [chips, setChips] = useState<Chip[]>(seed);
  const [tagNames, setTagNames] = useState<Record<string, string>>({});
  // the rule planner runs once per query; its filters become chips and the rest of the words stay the query (05 §2.3)
  const [planned, setPlanned] = useState<{ text: string; dates: string[]; chips: Chip[] } | null>(null);
  const [sel, setSel] = useState<{ all: boolean; ids: Set<string>; anchor: number | null; multi?: boolean }>({ all: false, ids: new Set(), anchor: null });
  const [toast, setToast] = useState<string | null>(null);
  const [comparing, setComparing] = useState<string[] | null>(null);
  const [showWeak, setShowWeak] = useState(false);
  const [viewingPossible, setViewingPossible] = useState<number | null>(null);
  useEffect(() => {
    setShowWeak(false);
    setChips(seed);
    setPlanned(null);
    setSel({ all: false, ids: new Set(), anchor: null });
  }, [q, seed]);

  // the planned answer is the first one: while its chips stand as planned, ask the same question again for each page,
  // not a second search for the same answer (SRCH-UI-035); a chip the user changes asks anew
  const body: SearchBody = useMemo(
    () => (planned && sameChips(chips, planned.chips) ? bodyFor(q, [], null) : bodyFor(q, chips, planned)),
    [q, chips, planned],
  );
  const res = useInfiniteQuery({
    queryKey: ["search", body],
    queryFn: ({ pageParam }) => api.search({ ...body, limit: 100, cursor: pageParam, facets: ["tags", "year"], lang: i18n.language }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    retry: searchRetry, // a failed page says so in a second, not after three tries (SRCH-UI-047)
  });
  const plannerOut = res.data?.pages[0]?.planner;
  useEffect(() => {
    if (planned || !plannerOut) return;
    const auto = chipsFrom(plannerOut);
    setChips(auto);
    setPlanned({ text: plannerOut.text, dates: dateWords(plannerOut.explain), chips: auto });
  }, [plannerOut, planned]);
  const index = useQuery({ queryKey: ["search-index"], queryFn: api.searchIndex, refetchInterval: 5000 });
  const first = res.data?.pages[0];
  const hits = (res.data?.pages ?? []).flatMap((p) => p.items);
  const total = first?.total ?? 0;
  const count = sel.all ? total : sel.multi ? sel.ids.size : 0; // one clicked photo is not a batch

  const gridRef = useRef<HTMLDivElement>(null);
  // the results are a grid like the timeline (10 §5.5): one tab stop, arrows move, Space selects, Enter opens
  const [active, setActive] = useState(0);
  const [focusAt, setFocusAt] = useState<number | null>(null); // the first new photo after 載入更多 (SRCH-UI-041)
  const [viewing, setViewing] = useState<number | null>(null);
  const tiles = () => [...(gridRef.current?.querySelectorAll<HTMLElement>('[role="gridcell"]') ?? [])];
  const focusTile = (i: number) => {
    const all = tiles();
    const j = Math.max(0, Math.min(i, all.length - 1));
    setActive(j);
    all[j]?.focus();
    return j;
  };
  const [loadedNote, setLoadedNote] = useState("");
  useEffect(() => {
    if (focusAt !== null && tiles().length > focusAt) {
      focusTile(focusAt);
      setFocusAt(null);
      // a page that came is said, for a screen reader too (SRCH-UI-049)
      setLoadedNote(t("search.loadedNote", { n: tiles().length.toLocaleString(i18n.language), total: (first?.total ?? 0).toLocaleString(i18n.language) }));
    }
  });
  const columns = () => {
    const all = tiles();
    const top = all[0]?.offsetTop;
    return Math.max(1, all.filter((el) => el.offsetTop === top).length);
  };
  const gridApi: GridApi = useMemo(
    () => ({
      total: hits.length,
      ordinal: (p) => p.index,
      at: async (i) => {
        const h = hits[i];
        if (!h) return null;
        return {
          asset: gridAssetOf(h), // with its saved angle (ROT-005)
          pos: { bucket: 0, index: i },
        };
      },
    }),
    [hits],
  );
  // Esc clears the selection wherever focus is but a text field, as on the timeline (SRCH-UI-026: after 復原 focus
  // was on the page and Esc did nothing, so the next Ctrl-clicks took photos out instead of adding them)
  const choosingNow = sel.all || sel.ids.size > 0;
  useEffect(() => {
    if (!choosingNow || viewing !== null) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || typingIn(e.target) || e.defaultPrevented) return;
      if ((e.target as HTMLElement | null)?.closest?.('[role="dialog"], [role="alertdialog"], [role="menu"]')) return; // theirs
      setSel({ all: false, ids: new Set(), anchor: null });
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [choosingNow, viewing]);
  const onKey = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "a") {
      e.preventDefault();
      setSel({ all: true, ids: new Set(), anchor: sel.anchor, multi: true }); // every result, rendered or not (E2E-SEL-001)
      return;
    }
    if (e.key === "Escape") {
      setSel({ all: false, ids: new Set(), anchor: null });
      return;
    }
    const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: columns(), ArrowUp: -columns() }[e.key];
    if (step !== undefined || e.key === "Home" || e.key === "End") {
      e.preventDefault();
      const to = e.key === "Home" ? 0 : e.key === "End" ? hits.length - 1 : active + (step ?? 0);
      const j = focusTile(to);
      if (e.shiftKey && hits[j]) clickHit(j, hits[j].asset_id, true); // Shift+arrow extends the selection
      return;
    }
    if (e.key === " " && hits[active]) {
      e.preventDefault();
      clickHit(active, hits[active].asset_id, e.shiftKey, !e.shiftKey);
      return;
    }
    if (e.key === "Enter" && hits[active]) {
      e.preventDefault();
      setViewing(active);
    }
  };
  const clickHit = (i: number, id: string, shift: boolean, toggle = false) => {
    if (toggle) {
      // ⌘/Ctrl-click adds or removes one photo (10 §4.1); the photo last opened is not part of it
      const ids = new Set(sel.all || !sel.multi ? [] : sel.ids);
      if (ids.has(id)) ids.delete(id);
      else ids.add(id);
      setSel({ all: false, ids, anchor: i, multi: true });
    } else if (shift && sel.anchor !== null) {
      const [a, b] = [Math.min(sel.anchor, i), Math.max(sel.anchor, i)];
      const ids = new Set(sel.multi ? sel.ids : []);
      for (const h of hits.slice(a, b + 1)) ids.add(h.asset_id);
      setSel({ all: false, ids, anchor: i, multi: true });
    } else if (shift) {
      const ids = new Set(sel.multi ? sel.ids : []);
      ids.add(id);
      setSel({ all: false, ids, anchor: i, multi: true });
    } else if (sel.all || sel.multi) {
      // already choosing photos: a click adds or removes one, as in Google Photos (SRCH-UI-011)
      const ids = new Set(sel.all ? [] : sel.ids);
      if (ids.has(id)) ids.delete(id);
      else ids.add(id);
      setSel({ all: false, ids, anchor: i, multi: true });
    } else {
      // a plain click opens the photo; it also becomes the current one, outlined (A11Y-005), not a batch to act on
      setSel({ all: false, ids: new Set([id]), anchor: i, multi: false });
      setActive(i);
      setViewing(i);
    }
  };
  const handle = async () =>
    sel.all ? api.createSelection({ query: body }) : api.createSelection({ asset_ids: [...sel.ids] });

  const soft = first ? softView(t, first.soft, total, chips, showWeak ? 0 : (first.weak ?? 0)) : null;
  const weak = first ? weakTail(t, first.weak_from, first.weak ?? 0, showWeak, hits.length) : null;
  const shownHits = weak ? hits.slice(0, weak.from) : hits;
  const loadWeak = weakNeedsMore(showWeak && Boolean(first?.weak), hits.length, total, Boolean(res.hasNextPage), res.isFetchingNextPage);
  useEffect(() => {
    if (loadWeak) void res.fetchNextPage(); // all the folded ones, not one more page (SRCH-SEM-009)
  }, [loadWeak, res]);
  return (
    <section className="space-y-4 p-4" aria-labelledby="search-h">
      <div className="flex items-baseline gap-3">
        <h1 id="search-h" className="text-lg font-semibold">
          {/* a memory theme's photos are titled with the theme (audit round 83) */}
          {tag && year && !q && chips.some((c) => c.kind === "tag" && c.value === tag) && chips.some((c) => c.kind === "year" && c.value === year) ? t("memories.themeTitle", { year, name: { ...tagNames, ...first?.tag_names }[tag] ?? name ?? tag }) : q ? t("search.resultsFor", { q }) : t("search.results")}
        </h1>
        {first && (total > 0 || !first.possible_total) ? (
          // no count until the answer is here: 「0 張」 flashed before every result (audit round 2); none over
          // 「沒有確定符合的照片」 either, which says it (SRCH-UI-036)
          <span data-testid="result-count" className="tnum text-[var(--text-muted)]">
            {headerCount(t, total, first.weak ?? 0, Boolean(first.total_capped), i18n.language, first.meaning_only ? [] : first.relaxed)}
            {first.collapsed ? ` · ${t("search.collapsed", { n: first.collapsed.toLocaleString(i18n.language) })}` : ""}
          </span>
        ) : null}
      </div>
      {soft?.note ? (
        <p role="status" className="text-sm text-[var(--text-muted)]">{soft.note}</p>
      ) : null}
      {first && relaxedText(t, first.relaxed, Boolean(first.meaning_only), total) ? (
        <p role="status" className="flex flex-wrap items-center gap-2 text-sm text-[var(--text-muted)]">
          <span>{relaxedText(t, first.relaxed, Boolean(first.meaning_only), total)}</span>
          {total > 0 && q ? (
            // part of the question went unanswered: the whole question can still go to 問 AI (SRCH-UI-033)
            <button type="button" className="chip" onClick={() => askAbout(q)}>
              {t("search.askInstead", { q })}
            </button>
          ) : null}
        </p>
      ) : null}
      {index.data && index.data.total > 0 && index.data.searchable < index.data.total && (
        <p data-testid="index-banner" role="status" className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2">
          {t("search.indexing", { have: index.data.searchable.toLocaleString(i18n.language), total: index.data.total.toLocaleString(i18n.language) })}
        </p>
      )}
      {isDegraded(first) && <p role="status">{t("search.degraded")}</p>}
      <ul aria-label={t("search.chips")} className="flex flex-wrap gap-2">
        {chips.map((c) => (
          <li key={`${c.kind}:${c.value}`} className="flex items-center gap-1 rounded-full border border-[var(--border)] px-3 py-0.5 text-sm">
            {chipLabel(t, c, { ...tagNames, ...first?.tag_names })}
            <button type="button" aria-label={t("search.remove", { what: chipLabel(t, c, { ...tagNames, ...first?.tag_names }) })} onClick={(e) => {
                // focus stays in the chips, on the next one, or the search box (SRCH-UI-043)
                const list = e.currentTarget.closest("ul");
                const at = [...(list?.querySelectorAll("button") ?? [])].indexOf(e.currentTarget);
                requestAnimationFrame(() => {
                  const left = [...(list?.querySelectorAll<HTMLButtonElement>("button") ?? [])];
                  (left[Math.min(at, left.length - 1)] ?? document.querySelector<HTMLInputElement>('[role="search"] input'))?.focus();
                });
                return (
                (c.kind === "tag" && c.value === tag) || (c.kind === "year" && c.value === year)
                  ? // a theme's filter came from the address: it leaves the page and the address (SRCH-UI-039/040)
                    (setChips(chips.filter((x) => x !== c)),
                    void nav({
                      to: "/search",
                      search: { q, ...(tag && !(c.kind === "tag") ? { tag, ...(name ? { name } : {}) } : {}), ...(year && !(c.kind === "year") ? { year } : {}) },
                    }))
                  : c.kind === "person"
                  ? void nav({ to: "/search", search: { q: withoutPerson(q, c.value) } }) // the box follows (SRCH-UI-013)
                  : (c.kind === "range" || c.kind === "day") && planned?.dates.length && withoutWords(q, planned.dates) !== q
                    ? void nav({ to: "/search", search: { q: withoutWords(q, planned.dates) } }) // the date leaves the box too (SRCH-UI-021)
                    : setChips(chips.filter((x) => x !== c)));
              }}>
              ×
            </button>
          </li>
        ))}
      </ul>
      {/* one row that scrolls sideways: two wrapped rows of chips made the header busy (audit round 26) */}
      <div className="facet-row">
        <Facet
          label={t("search.tags")}
          values={first?.facets.tags}
          names={{ ...tagNames, ...first?.tag_names }}
          onPick={(v) => {
            if (first?.tag_names?.[v]) setTagNames((x) => ({ ...x, [v]: first.tag_names![v] }));
            if (!chips.some((c) => c.kind === "tag" && c.value === v)) setChips([...chips, { kind: "tag", value: v }]);
          }}
        />
        <Facet
          label={t("search.years")}
          values={first?.facets.year}
          onPick={(v) => setChips([...chips.filter((c) => c.kind !== "year"), { kind: "year", value: v }])}
        />
      </div>
      {count > 0 && (
        <BatchBar
          count={count}
          makeSelection={handle}
          onDone={(n) => setToast(t("batch.updated", { n: n.toLocaleString(i18n.language) }))}
          onCompare={sel.all ? undefined : () => setComparing([...sel.ids])}
        />
      )}
      {comparing ? (
        <CompareView
          ids={comparing}
          onClose={() => {
            setComparing(null);
            window.setTimeout(() => document.querySelector<HTMLElement>("[data-compare-button]")?.focus(), 0);
          }}
        />
      ) : null}
      {res.isPending ? (
        // the first answer can take a few seconds after the app idles: say so, and hold the grid's place (SRCH-UI-020)
        <>
          <p role="status" aria-label={t("search.busyLabel")} className="text-sm text-[var(--text-muted)]">
            {t("search.busy")}
          </p>
          <div data-testid="search-skeleton" aria-busy="true" className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-0.5">
            {Array.from({ length: 12 }, (_, i) => (
              <div key={i} className="search-skeleton aspect-square" />
            ))}
          </div>
        </>
      ) : null}
      {res.isSuccess && hits.length === 0 && <p>{t(first?.possible_total ? "search.noneSure" : "search.none")}</p>}
      {res.isSuccess && hits.length === 0 ? (
        // nothing found: the words inside the query that do find photos (SRCH-UI-030), and the same words for 問 AI
        // (SRCH-UI-031): a page that finds nothing still has a next step, with or without 「可能相關」 (SRCH-UI-034)
        <p data-testid="suggest" className="flex flex-wrap items-center gap-2 text-sm text-[var(--text-muted)]">
          {first?.suggest?.length ? <span>{t("search.tryInstead")}</span> : null}
          {(first?.suggest ?? []).map((x) => (
            <button
              key={x.q}
              type="button"
              className="chip"
              onClick={() => void nav({ to: "/search", search: { q: x.q } })}
            >
              {t("search.tryWord", { q: x.q, n: x.n })}
            </button>
          ))}
          {q ? (
            <button type="button" className="chip" onClick={() => askAbout(q)}>
              {t("search.askInstead", { q })}
            </button>
          ) : null}
        </p>
      ) : null}
      <div ref={gridRef} onKeyDown={onKey} role="grid" aria-label={t("search.results")} aria-multiselectable="true" aria-rowcount={-1} className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-0.5 outline-none">
        <div role="row" className="contents">
        {shownHits.map((h, i) => {
          const selected = sel.all || sel.ids.has(h.asset_id);
          return (
            <Fragment key={h.asset_id}>
            {soft?.at === i ? (
              // where the photos that show the expression end (SRCH-CNC-010)
              <div role="presentation" data-testid="soft-divider" className="col-span-full flex flex-wrap items-center gap-3 py-2 text-sm text-[var(--text-muted)]">
                <span>{soft.divider}</span>
                <button type="button" className="underline" onClick={() => setChips([...chips, soft.chip])}>
                  {soft.only}
                </button>
              </div>
            ) : null}
            <button
              key={h.asset_id}
              type="button"
              role="gridcell"
              aria-selected={selected}
              aria-label={tileLabel(t, h)}
              tabIndex={i === active ? 0 : -1}
              data-testid="search-result"
              data-asset={h.asset_id}
              className="tile relative aspect-square"
              style={{ position: "relative" }}
              onFocus={() => setActive(i)}
              onClick={(e) => clickHit(i, h.asset_id, e.shiftKey, e.ctrlKey || e.metaKey)}
            >
              <img src={mediaUrl(h.asset_id, "t256", h.ch)} alt="" loading="lazy" style={turnStyle(h.rot)} onError={(e) => (e.currentTarget.style.visibility = "hidden")} />
              {h.duplicates ? (
                // copies of this photo, folded into it (SRCH-UI-024): they were hidden without a word
                <span className="tile-copies tnum" aria-hidden="true" title={t("search.copies", { n: h.duplicates })}>
                  +{h.duplicates.toLocaleString(i18n.language)}
                </span>
              ) : null}
              <span data-matched={h.matched_by.join(" ")} className="absolute bottom-1 left-1 text-[10px]">
                {/* found by how it looks, not by its words: worth knowing (E2E-S-002); text and tag matches need no badge */}
                {onlyByMeaning(h.matched_by) ? <span className="rounded bg-black/55 px-1 text-white/90">{t("search.matched.image_vector")}</span> : null}
              </span>
            </button>
            </Fragment>
          );
        })}
        </div>
      </div>
      {viewing !== null ? (
        <Lightbox
          start={viewing}
          grid={gridApi}
          onClose={(id) => {
            setViewing(null);
            const i = Math.max(0, hits.findIndex((h) => h.asset_id === id));
            window.setTimeout(() => focusTile(id ? i : active), 0); // focus goes back to the photo you were on
          }}
        />
      ) : null}
      {weak ? (
        <Button
          variant="ghost"
          data-testid="weak-more"
          onClick={() => setShowWeak(true)}
        >
          {weak.more}
        </Button>
      ) : null}
      {first && !weak && cappedNote(t, total, Boolean(first.total_capped), Boolean(res.hasNextPage)) ? (
        <p data-testid="capped-note" className="text-sm text-[var(--text-muted)]">
          {cappedNote(t, total, Boolean(first.total_capped), Boolean(res.hasNextPage))}
        </p>
      ) : null}
      <p role="status" className="sr-only">{loadedNote}</p>
      {res.isFetchNextPageError ? (
        // a page that did not come says so, with a way to try again (SRCH-UI-046: the button did nothing)
        <div className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
          {/* the alert says what failed; the button is beside it, not read as part of it (audit round 106) */}
          <p role="alert">{res.isFetchingNextPage ? "" : t("search.moreFailed")}</p>
          <button
            type="button"
            className="chip"
            ref={(el) => {
              // the failure takes the place of 載入更多: focus goes with it (SRCH-UI-048)
              if (el && !res.isFetchingNextPage && document.activeElement === document.body) el.focus();
            }}
            onClick={() => {
              if (res.isFetchingNextPage) return;
              setFocusAt(hits.length);
              void res.fetchNextPage();
            }}
          >
            {res.isFetchingNextPage ? t("search.retrying") : t("search.retry")}
          </button>
        </div>
      ) : null}
      {!weak && res.hasNextPage && !res.isFetchNextPageError && (
        <Button
          variant="ghost"
          onClick={() => {
            // the first new photo takes focus: it dropped to the page (SRCH-UI-041)
            if (res.isFetchingNextPage) return; // a press while a page loads sends nothing more (SRCH-UI-048)
            setFocusAt(hits.length); // focused once that photo is drawn, however long the page takes
            void res.fetchNextPage();
          }}
        >
          {res.isFetchingNextPage ? t("search.loadingMore") : loadMoreText(t, total, hits.length, Boolean(first?.total_capped), i18n.language)}
        </Button>
      )}
      {first?.possible_total ? (
        // 「可能相關」 (ADR-0047): found by the image or caption models only, unlike the photos the words found; apart
        // from the count, the facets and 全選, folded until asked for
        <details data-testid="possible" className="possible-group space-y-2">
          <summary className="cursor-pointer text-sm text-[var(--text-muted)]">
            {t("search.possible", { n: first.possible_total.toLocaleString(i18n.language) })}
          </summary>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-0.5">
            {(first.possible ?? []).map((h, i) => (
              <button
                key={h.asset_id}
                type="button"
                className="tile relative aspect-square opacity-90"
                style={{ position: "relative" }} // in flow: .tile is absolutely positioned on the timeline (audit round 68)
                aria-label={tileLabel(t, h)}
                onClick={() => setViewingPossible(i)}
              >
                <img src={mediaUrl(h.asset_id, "t256", h.ch)} alt="" loading="lazy" />
              </button>
            ))}
          </div>
        </details>
      ) : null}
      {viewingPossible !== null && first?.possible ? (
        <Lightbox start={viewingPossible} grid={possibleGrid(first.possible)} onClose={() => setViewingPossible(null)} />
      ) : null}
      {toast && (
        <div role="status" className="toast" onAnimationEnd={() => undefined}>
          {toast}
        </div>
      )}
    </section>
  );
}

export function Facet({ label, values, names, onPick }: { label: string; values?: Record<string, number>; names?: Record<string, string>; onPick: (v: string) => void }) {
  if (!values || Object.keys(values).length === 0) return null;
  const top = Object.entries(values)
    .sort((a, b) => b[1] - a[1] || b[0].localeCompare(a[0]))
    .slice(0, 12);
  return (
    <div role="group" aria-label={label} className="flex shrink-0 items-center gap-1 text-sm">
      <span className="text-[var(--text-muted)]">{label}</span>
      {top.map(([v, n]) => (
        <button key={v} type="button" data-value={v} onClick={() => onPick(v)} className="facet-chip">
          {names?.[v] ?? v} <span className="tnum text-[var(--text-muted)]">{n}</span>
        </button>
      ))}
    </div>
  );
}

function BatchBar({
  count,
  makeSelection,
  onDone,
  onCompare,
}: {
  count: number;
  makeSelection: () => Promise<{ id: string; count: number }>;
  onDone: (n: number) => void;
  onCompare?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [tag, setTag] = useState("");
  const [cat, setCat] = useState("");
  const preset = useQuery({ queryKey: ["preset", "by-content-12", i18n.language], queryFn: () => api.preset("by-content-12", i18n.language) });
  const labels = useMutation({
    mutationFn: async (body: { category?: string; tags?: string[] }) => {
      const s = await makeSelection();
      return api.selectionLabels(s.id, body);
    },
    onSuccess: (r) => onDone(r.updated),
  });
  const busy = labels.isPending ? { code: "BUSY", message: t("common.working") } : null;
  return (
    <div data-testid="batch-bar" className="flex flex-wrap items-end gap-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-3">
      <strong className="tnum">{t("batch.selected", { n: count.toLocaleString(i18n.language) })}</strong>
      <Button
        variant="ghost"
        data-compare-button=""
        disabledReason={
          !onCompare || count < 2
            ? { code: "COMPARE_TOO_FEW", message: t("compare.tooFew") }
            : count > COMPARE_MAX
              ? { code: "COMPARE_TOO_MANY", message: t("compare.tooMany", { n: COMPARE_MAX }) }
              : null
        }
        onClick={() => onCompare?.()}
      >
        {t("compare.open")}
      </Button>
      {/* ★ and 加入相簿 for the selection (COL-015): only organising and tags were offered (audit round 49) */}
      <FavoriteSelection put={async (aid) => api.selectionAlbum((await makeSelection()).id, aid)} />
      <AddToAlbum put={async (aid) => api.selectionAlbum((await makeSelection()).id, aid)} />
      <label className="flex flex-col text-xs">
        {t("batch.tag")}
        <input aria-label={t("batch.tag")} value={tag} onChange={(e) => setTag(e.target.value)} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-sm" />
      </label>
      {tag.trim() ? (
        <Button variant="ghost" disabledReason={busy} onClick={() => labels.mutate({ tags: [tag.trim()] })}>
          {t("batch.addTag")}
        </Button>
      ) : null}
      <label className="flex flex-col text-xs">
        {t("batch.category")}
        <select aria-label={t("batch.category")} value={cat} onChange={(e) => setCat(e.target.value)} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-sm">
          <option value="">—</option>
          {(preset.data?.categories ?? []).map((c) => (
            <option key={c.key} value={c.key}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      {cat ? (
        <Button variant="ghost" disabledReason={busy} onClick={() => labels.mutate({ category: cat })}>
          {t("batch.applyCategory")}
        </Button>
      ) : null}
      <TrashButton select={makeSelection} />
      <CopyToFolderButton select={makeSelection} />
    </div>
  );
}

/** The 「可能相關」 photos as a Lightbox's list (ADR-0047). */
function possibleGrid(list: PossibleHit[]): GridApi {
  return {
    total: list.length,
    ordinal: (p) => p.index,
    at: async (i) => (list[i] ? { asset: { id: list[i].asset_id, ch: list[i].ch, w: null, h: null, thumbhash: null, taken_at: list[i].taken_at, flags: [] }, pos: { bucket: 0, index: i } } : null),
  };
}

