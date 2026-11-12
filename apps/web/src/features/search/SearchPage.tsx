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
import { api, isDegraded, mediaUrl, type SearchBody } from "../../lib/api";
import { turnStyle } from "../../lib/turn";

import { AddToAlbum, FavoriteSelection, typingIn } from "../albums/AlbumButtons";
import { TrashButton } from "../../components/TrashButton";
import { bodyFor, cappedNote, chipLabel, chipsFrom, gridAssetOf, headerCount, onlyByMeaning, relaxedText, softView, weakNeedsMore, tileLabel, weakTail, withoutPerson, withoutWords, dateWords, type Chip } from "./searchPlan";

export function SearchPage({ q }: { q: string }) {
  const { t, i18n } = useTranslation();
  const nav = useNavigate();
  const [chips, setChips] = useState<Chip[]>([]);
  const [tagNames, setTagNames] = useState<Record<string, string>>({});
  // the rule planner runs once per query; its filters become chips and the rest of the words stay the query (05 §2.3)
  const [planned, setPlanned] = useState<{ text: string; dates: string[] } | null>(null);
  const [sel, setSel] = useState<{ all: boolean; ids: Set<string>; anchor: number | null; multi?: boolean }>({ all: false, ids: new Set(), anchor: null });
  const [toast, setToast] = useState<string | null>(null);
  const [comparing, setComparing] = useState<string[] | null>(null);
  const [showWeak, setShowWeak] = useState(false);
  useEffect(() => {
    setShowWeak(false);
    setChips([]);
    setPlanned(null);
    setSel({ all: false, ids: new Set(), anchor: null });
  }, [q]);

  const body: SearchBody = useMemo(() => bodyFor(q, chips, planned), [q, chips, planned]);
  const res = useInfiniteQuery({
    queryKey: ["search", body],
    queryFn: ({ pageParam }) => api.search({ ...body, limit: 100, cursor: pageParam, facets: ["tags", "year"], lang: i18n.language }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const plannerOut = res.data?.pages[0]?.planner;
  useEffect(() => {
    if (planned || !plannerOut) return;
    setChips(chipsFrom(plannerOut));
    setPlanned({ text: plannerOut.text, dates: dateWords(plannerOut.explain) });
  }, [plannerOut, planned]);
  const index = useQuery({ queryKey: ["search-index"], queryFn: api.searchIndex, refetchInterval: 5000 });
  const first = res.data?.pages[0];
  const hits = (res.data?.pages ?? []).flatMap((p) => p.items);
  const total = first?.total ?? 0;
  const count = sel.all ? total : sel.multi ? sel.ids.size : 0; // one clicked photo is not a batch

  const gridRef = useRef<HTMLDivElement>(null);
  // the results are a grid like the timeline (10 §5.5): one tab stop, arrows move, Space selects, Enter opens
  const [active, setActive] = useState(0);
  const [viewing, setViewing] = useState<number | null>(null);
  const tiles = () => [...(gridRef.current?.querySelectorAll<HTMLElement>('[role="gridcell"]') ?? [])];
  const focusTile = (i: number) => {
    const all = tiles();
    const j = Math.max(0, Math.min(i, all.length - 1));
    setActive(j);
    all[j]?.focus();
    return j;
  };
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
        <h1 id="search-h" className="text-lg font-semibold">{t("search.results")}</h1>
        {first ? (
          // no count until the answer is here: 「0 張」 flashed before every result (audit round 2)
          <span data-testid="result-count" className="tnum text-[var(--text-muted)]">
            {headerCount(t, total, first.weak ?? 0, Boolean(first.total_capped), i18n.language, first.meaning_only ? [] : first.relaxed)}
          </span>
        ) : null}
      </div>
      {soft?.note ? (
        <p role="status" className="text-sm text-[var(--text-muted)]">{soft.note}</p>
      ) : null}
      {first && relaxedText(t, first.relaxed, Boolean(first.meaning_only)) ? (
        <p role="status" className="text-sm text-[var(--text-muted)]">{relaxedText(t, first.relaxed, Boolean(first.meaning_only))}</p>
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
            {chipLabel(t, c, tagNames)}
            <button type="button" aria-label={t("search.remove", { what: chipLabel(t, c, tagNames) })} onClick={() =>
                c.kind === "person"
                  ? void nav({ to: "/search", search: { q: withoutPerson(q, c.value) } }) // the box follows (SRCH-UI-013)
                  : (c.kind === "range" || c.kind === "day") && planned?.dates.length && withoutWords(q, planned.dates) !== q
                    ? void nav({ to: "/search", search: { q: withoutWords(q, planned.dates) } }) // the date leaves the box too (SRCH-UI-021)
                    : setChips(chips.filter((x) => x !== c))
              }>
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
      {res.isSuccess && hits.length === 0 && <p>{t("search.none")}</p>}
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
      {!weak && res.hasNextPage && (
        <Button variant="ghost" onClick={() => void res.fetchNextPage()}>
          {t("search.loadMore")}
        </Button>
      )}
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
  const nav = useNavigate();
  const [tag, setTag] = useState("");
  const [cat, setCat] = useState("");
  const [moving, setMoving] = useState(false);
  const moveRef = useRef<HTMLSpanElement>(null);
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
      <span ref={moveRef}>
        <Button variant="ghost" onClick={() => setMoving(true)}>
          {t("batch.moveToFolder")}
        </Button>
      </span>
      {moving && (
        <MoveToFolder
          onCancel={() => {
            setMoving(false);
            requestAnimationFrame(() => moveRef.current?.querySelector("button")?.focus()); // back where it opened
          }}
          onCreate={async (target) => {
            const s = await makeSelection();
            const r = await api.selectionOrganize(s.id, target);
            void nav({ to: "/plans/$planId", params: { planId: r.plan_id } });
          }}
        />
      )}
    </div>
  );
}

function MoveToFolder({ onCancel, onCreate }: { onCancel: () => void; onCreate: (t: { root_alias: string; sub_path: string }) => Promise<void> }) {
  const { t } = useTranslation();
  const roots = useQuery({ queryKey: ["roots"], queryFn: api.roots });
  const writable = (roots.data ?? []).filter((r) => r.mode === "rw");
  const [root, setRoot] = useState<string>("");
  const [sub, setSub] = useState("");
  const create = useMutation({ mutationFn: () => onCreate({ root_alias: root || writable[0]?.alias, sub_path: sub }) });
  // the dialog takes the keyboard as it opens (audit round 58: focus stayed on the button behind it)
  const first = useRef<HTMLSelectElement>(null);
  useEffect(() => first.current?.focus(), []);
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="mtf-h" className="fixed inset-0 z-40 grid place-items-center bg-black/50" onKeyDown={(e) => e.key === "Escape" && onCancel()}>
      <div className="w-96 space-y-3 rounded-[var(--radius-lg)] bg-[var(--surface)] p-6">
        <h2 id="mtf-h" className="text-lg font-semibold">{t("batch.moveTitle")}</h2>
        <p className="text-sm text-[var(--text-muted)]">{t("batch.copyNote")}</p>
        <label className="flex flex-col text-sm">
          {t("batch.root")}
          <select ref={first} value={root || writable[0]?.alias || ""} onChange={(e) => setRoot(e.target.value)} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1">
            {writable.map((r) => (
              <option key={r.alias} value={r.alias}>
                {r.alias}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm">
          {t("batch.subPath")}
          <input value={sub} onChange={(e) => setSub(e.target.value)} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1" />
        </label>
        <div className="flex gap-2">
          <Button
            disabledReason={!writable.length ? { code: "FS_NOT_WRITABLE", message: t("organize.noWritableTarget") } : create.isPending ? { code: "BUSY", message: t("common.working") } : null}
            onClick={() => create.mutate()}
          >
            {t("batch.createPlan")}
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            {t("batch.cancel")}
          </Button>
        </div>
      </div>
    </div>
  );
}
