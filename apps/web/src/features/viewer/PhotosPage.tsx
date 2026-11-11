import { lazy, Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";
import { AddToAlbum, FavoriteSelection } from "../albums/AlbumButtons";
import { memoryGrid, OnThisDay } from "../memories/OnThisDay";
import { Lightbox } from "./Lightbox";
import { PhotoGrid, type GridApi } from "./PhotoGrid";
import { NONE, pick, type Selection } from "./selection";
import { withTransition } from "./transition";

const FaceOptInCard = lazy(() => import("../people/PeoplePage").then((m) => ({ default: m.FaceOptInCard })));

export function PhotosPage() {
  const [open, setOpen] = useState<{ ord: number; grid: GridApi } | null>(null);
  const [back, setBack] = useState<string | null>(null);
  const [sel, setSel] = useState<Selection>(NONE);
  const choosing = sel.ids.size > 0;
  // back to the photo you were on: the bar's buttons go away with the selection and focus fell to the page (VIEW-041)
  const clear = () => {
    setSel(NONE);
    requestAnimationFrame(() => document.querySelector<HTMLElement>('[data-testid="photo-grid"] .tile[tabindex="0"]')?.focus());
  };
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
      <OnThisDay onOpen={(ids, start) => setOpen({ ord: start, grid: memoryGrid(ids) })} />
      {choosing ? <TimelineBatch ids={sel.ids} onClear={clear} /> : null}
      <div className="min-h-0 flex-1">
      <PhotoGrid
        focusAsset={back}
        selected={sel.ids}
        onPick={(id, how, order) => setSel((s) => pick(s, id, how, order))}
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
function TimelineBatch({ ids, onClear }: { ids: ReadonlySet<string>; onClear: () => void }) {
  const { t, i18n } = useTranslation();
  const put = async (albumId: string) => api.selectionAlbum((await api.createSelection({ asset_ids: [...ids] })).id, albumId);
  return (
    <div data-testid="timeline-batch" className="timeline-batch">
      <strong className="tnum">{t("batch.selected", { n: ids.size.toLocaleString(i18n.language) })}</strong>
      <FavoriteSelection put={put} />
      <AddToAlbum put={put} />
      <button type="button" className="ml-auto text-sm underline" onClick={onClear}>
        {t("batch.clear")} <kbd className="text-xs text-[var(--text-muted)]">Esc</kbd>
      </button>
    </div>
  );
}
