import { lazy, Suspense, useState } from "react";
import { OnThisDay } from "../memories/OnThisDay";
import { Lightbox } from "./Lightbox";
import { PhotoGrid, type GridApi } from "./PhotoGrid";
import { withTransition } from "./transition";

const FaceOptInCard = lazy(() => import("../people/PeoplePage").then((m) => ({ default: m.FaceOptInCard })));

/** A Lightbox over a list of photos, the 那年今日 row's (10 §2.8). */
function idsGrid(photos: Array<{ id: string; rot?: number }>): GridApi {
  return {
    total: photos.length,
    ordinal: (p) => p.index,
    at: async (i) =>
      photos[i]
        ? { asset: { id: photos[i].id, w: null, h: null, thumbhash: null, taken_at: null, flags: [], rot: photos[i].rot }, pos: { bucket: 0, index: i } }
        : null,
  };
}

export function PhotosPage() {
  const [open, setOpen] = useState<{ ord: number; grid: GridApi } | null>(null);
  const [back, setBack] = useState<string | null>(null);
  return (
    <div className="flex h-full flex-col">
      {/* here, not above the page: a card outside the grid's box pushed its bottom past the window */}
      <Suspense fallback={null}>
        <FaceOptInCard />
      </Suspense>
      <OnThisDay onOpen={(ids, start) => setOpen({ ord: start, grid: idsGrid(ids) })} />
      <div className="min-h-0 flex-1">
      <PhotoGrid
        focusAsset={back}
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
