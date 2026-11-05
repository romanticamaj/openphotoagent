import { lazy, Suspense, useState } from "react";
import { Lightbox } from "./Lightbox";
import { PhotoGrid, type GridApi } from "./PhotoGrid";
import { withTransition } from "./transition";

const FaceOptInCard = lazy(() => import("../people/PeoplePage").then((m) => ({ default: m.FaceOptInCard })));

export function PhotosPage() {
  const [open, setOpen] = useState<{ ord: number; grid: GridApi } | null>(null);
  const [back, setBack] = useState<string | null>(null);
  return (
    <div className="flex h-full flex-col">
      {/* here, not above the page: a card outside the grid's box pushed its bottom past the window */}
      <Suspense fallback={null}>
        <FaceOptInCard />
      </Suspense>
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
