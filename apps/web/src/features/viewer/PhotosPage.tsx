import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";
import { AddToAlbum, FavoriteSelection, typingIn } from "../albums/AlbumButtons";
import { TrashButton } from "../../components/TrashButton";
import { memoryGrid, OnThisDay } from "../memories/OnThisDay";
import { Lightbox } from "./Lightbox";
import { PhotoGrid, type GridApi } from "./PhotoGrid";
import { all, chosen, countOf, NONE, pick, span, type Selection } from "./selection";
import { withTransition } from "./transition";

const FaceOptInCard = lazy(() => import("../people/PeoplePage").then((m) => ({ default: m.FaceOptInCard })));

export function PhotosPage() {
  const [open, setOpen] = useState<{ ord: number; grid: GridApi } | null>(null);
  const [back, setBack] = useState<string | null>(null);
  const [sel, setSel] = useState<Selection>(NONE);
  // the choice as it was when the current Shift range began: the range is added to it afresh at every step (VIEW-045)
  const spanBase = useRef<{ anchor: string; base: Selection } | null>(null);
  const [scrolledAway, setScrolledAway] = useState(false); // 那年今日 steps aside once the timeline scrolls (MEM-009)
  const choosing = Boolean(sel.all) || sel.ids.size > 0;
  // back to the photo you were on: the bar's buttons go away with the selection and focus fell to the page (VIEW-041)
  const clear = () => {
    setSel(NONE);
    requestAnimationFrame(() => document.querySelector<HTMLElement>('[data-testid="photo-grid"] .tile[tabindex="0"]')?.focus());
  };
  // Ctrl/⌘+A anywhere on the page but a text field: every photo, never the page's text (VIEW-044)
  useEffect(() => {
    if (open) return;
    const selectAll = (e: KeyboardEvent) => {
      if ((e.key !== "a" && e.key !== "A") || !(e.ctrlKey || e.metaKey) || typingIn(e.target)) return;
      e.preventDefault();
      setSel(all());
    };
    window.addEventListener("keydown", selectAll);
    return () => window.removeEventListener("keydown", selectAll);
  }, [open]);
  useEffect(() => {
    if (!choosing || open) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && clear();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [choosing, open]);
  return (
    <div className="flex h-full flex-col">
      {/* here, not above the page: a card outside the grid's box pushed its bottom past the window */}
      <Suspense fallback={null}>
        <FaceOptInCard />
      </Suspense>
      <OnThisDay away={scrolledAway} onOpen={(ids, start) => setOpen({ ord: start, grid: memoryGrid(ids) })} />
      {choosing ? <TimelineBatch sel={sel} onClear={clear} /> : null}
      <div className="min-h-0 flex-1">
      <PhotoGrid
        focusAsset={back}
        chosen={(id) => chosen(sel, id)}
        choosing={choosing}
        onPick={(id, how, order) => {
          spanBase.current = null;
          setSel((s) => pick(s, id, how, order));
        }}
        onSpan={(anchor, to, order) =>
          setSel((s) => {
            if (spanBase.current?.anchor !== anchor) spanBase.current = { anchor, base: s };
            return span(spanBase.current.base, anchor, to, order);
          })
        }
        onPickAll={() => setSel(all())}
        onScrolledAway={setScrolledAway}
        onOpen={(pos, grid) => withTransition(() => setOpen({ ord: grid.ordinal(pos), grid }), document.activeElement)}
      />
      </div>
      {open && (
        <Lightbox
          start={open.ord}
          grid={open.grid}
          onClose={(id) => {
            setOpen(null);
            setBack(id);
          }}
        />
      )}
    </div>
  );
}

/** What chosen photos can do (VIEW-038): ★ and 加入相簿 at once, as on the search page (COL-015). */
function TimelineBatch({ sel, onClear }: { sel: Selection; onClear: () => void }) {
  const { t, i18n } = useTranslation();
  // the library's size, from the timeline's own months (already loaded): what 「全選」 counts
  const buckets = useQuery({ queryKey: ["timeline", "buckets"], queryFn: api.buckets });
  const total = (buckets.data?.buckets ?? []).reduce((n, b) => n + b.count, 0);
  const n = countOf(sel, total);
  const select = () =>
    sel.all
      ? api.createSelection({ query: { collapse_duplicates: false }, exclude: [...sel.ids] }) // every photo, as shown
      : api.createSelection({ asset_ids: [...sel.ids] });
  const put = async (albumId: string) => api.selectionAlbum((await select()).id, albumId);
  return (
    <div data-testid="timeline-batch" className="timeline-batch">
      <strong className="tnum">{t("batch.selected", { n: n.toLocaleString(i18n.language) })}</strong>
      <FavoriteSelection put={put} />
      <AddToAlbum put={put} />
      <TrashButton select={select} />
      <button type="button" className="ml-auto text-sm underline" onClick={onClear}>
        {t("batch.clear")} <kbd className="text-xs text-[var(--text-muted)]">Esc</kbd>
      </button>
    </div>
  );
}
